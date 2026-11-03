"""Single synthetic-only, cross-domain cutover rehearsal with rollback.

Vehicle, service and SKU/STOCK CSV verification, staging, activation and
reconciliation either commit together or leave NO new live/staged objects.
No customer ownership, prior repair history, documents, prices or PII move.
"""
import csv
from dataclasses import dataclass
from hashlib import sha256
import io
import os
from pathlib import Path
import stat
from tempfile import TemporaryDirectory
from uuid import UUID

import psycopg

from .auto_org_authority import organization_role_allows
from .auto_service_import import stage_synthetic_services, _load as _load_service_source
from .auto_service_activation import activate_synthetic_services
from .auto_vehicle_import_service import stage_authorized_synthetic_vehicles
from .auto_vehicle_promotion_service import promote_synthetic_vehicles
from .auto_inventory_activation import (
    activate_synthetic_inventory, reconcile_synthetic_inventory,
)
from scripts.auto_migration_pg_staging import (
    stage_synthetic_csv,reconcile_synthetic_staging,
)
from scripts.auto_vehicle_migration_preflight import (
    MAX_BYTES as VEHICLE_MAX_BYTES, inspect_vehicle_csv,
)


class SyntheticCutoverDenied(ValueError):
    """Generic code only; never leaks VIN, articles or import source records."""


@dataclass(frozen=True)
class SyntheticCutoverSources:
    vehicle_path: Path
    vehicle_sha256: str
    vehicle_batch_id: UUID
    service_path: Path
    service_sha256: str
    service_batch_id: UUID
    inventory_path: Path
    inventory_sha256: str
    inventory_batch_id: UUID


def _validate(token: str,organization_id: UUID,location_id: UUID,
              sources: SyntheticCutoverSources,synthetic_only: bool) -> None:
    if (synthetic_only is not True or type(token) is not str or not token
        or any(type(x) is not UUID for x in (organization_id,location_id))
        or type(sources) is not SyntheticCutoverSources
        or any(type(x) is not UUID for x in (
            sources.vehicle_batch_id,sources.service_batch_id,
            sources.inventory_batch_id))
        or len({
            sources.vehicle_batch_id,sources.service_batch_id,
            sources.inventory_batch_id
        })!=3
        or any(not isinstance(x, Path) for x in (
            sources.vehicle_path,sources.service_path,sources.inventory_path))
        or any(type(x) is not str or len(x)!=64
               or any(c not in "0123456789abcdef" for c in x)
               for x in (
                   sources.vehicle_sha256,sources.service_sha256,
                   sources.inventory_sha256))):
        raise SyntheticCutoverDenied("INVALID_INPUT")


def _authority(conn: psycopg.Connection,token: str,
               org: UUID,loc: UUID) -> None:
    if not (organization_role_allows(
        conn,token,org,"MANAGER",loc,for_mutation=True
    ) and organization_role_allows(
        conn,token,org,"WAREHOUSE",loc,for_mutation=True
    )):
        raise SyntheticCutoverDenied("FORBIDDEN")


def _verify_source_staging(conn: psycopg.Connection,
                           org: UUID, loc: UUID,
                           sources: SyntheticCutoverSources) -> None:
    """Read-only source-byte/shape/digest/row reconciliation for all three CSVs.

    All vehicle validation uses ONE O_NOFOLLOW snapshot. The existing
    service and inventory validators already read one secure snapshot.
    Source validation is deliberately repeated after staging to detect
    a file replacement before commit and during independent audit.
    """
    try:
        if not hasattr(os, "O_NOFOLLOW"):
            raise SyntheticCutoverDenied("SOURCE_UNAVAILABLE")
        fd=os.open(sources.vehicle_path,
                   os.O_RDONLY|os.O_NONBLOCK|os.O_NOFOLLOW)
        with os.fdopen(fd,"rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise SyntheticCutoverDenied("SOURCE_UNAVAILABLE")
            raw=stream.read(VEHICLE_MAX_BYTES+1)
        if (len(raw)>VEHICLE_MAX_BYTES or
            sha256(raw).hexdigest()!=sources.vehicle_sha256):
            raise SyntheticCutoverDenied("SOURCE_MISMATCH")
        with TemporaryDirectory(prefix="auto-reconcile-") as directory:
            snapshot=Path(directory)/"vehicle.csv"
            snapshot.write_bytes(raw)
            checked=inspect_vehicle_csv(snapshot)
        if not checked["ok"]:
            raise SyntheticCutoverDenied("SOURCE_MISMATCH")
        parsed=list(csv.DictReader(
            io.StringIO(raw.decode("utf-8-sig"),newline=""),strict=True,
        ))
        expected=[
            (number,org,loc,row["source_id"],row["source_system"],
             row["vin"],row["make"],row["model"])
            for number,row in enumerate(parsed,start=2)
        ]
    except (OSError,UnicodeError,csv.Error,KeyError,ValueError) as exc:
        raise SyntheticCutoverDenied("SOURCE_UNAVAILABLE") from None
    stored=conn.execute(
        """SELECT row_number,organization_id,location_id,source_id,
                  source_system,vin,make,model
           FROM auto_vehicle_migration_line WHERE batch_id=%s
           ORDER BY row_number""",
        (sources.vehicle_batch_id,),
    ).fetchall()
    if not expected or stored!=expected:
        raise SyntheticCutoverDenied("SOURCE_MISMATCH")

    # _load is strict, verifies a no-follow source snapshot and SHA-256.
    try:
        service_lines=_load_service_source(
            sources.service_path,sources.service_sha256,
        )
    except ValueError:
        raise SyntheticCutoverDenied("SOURCE_MISMATCH") from None
    stored_services=conn.execute(
        """SELECT row_number,source_system,source_id,code,title,duration_minutes
           FROM auto_service_import_line WHERE batch_id=%s ORDER BY row_number""",
        (sources.service_batch_id,),
    ).fetchall()
    if stored_services!=service_lines:
        raise SyntheticCutoverDenied("SOURCE_MISMATCH")

    # This dedicated reconciler is SELECT-only and checks all STOCK/SKU rows,
    # quantities, source order, hash and tenant against the staged PG batch.
    try:
        reconcile_synthetic_staging(
            conn,sources.inventory_path,organization_id=org,
            batch_id=sources.inventory_batch_id,
            expected_sha256=sources.inventory_sha256,
        )
    except ValueError:
        raise SyntheticCutoverDenied("SOURCE_MISMATCH") from None


def _read_promoted(conn: psycopg.Connection,org: UUID,loc: UUID,
                   sources: SyntheticCutoverSources) -> dict:
    # Reconcile against all three source manifests as well as the live
    # relational provenance. A caller cannot swap the expected file hash and
    # obtain a false success solely because row counts still match.
    vehicle_source=conn.execute(
        """SELECT organization_id,source_sha256,row_count
           FROM auto_vehicle_migration_batch WHERE id=%s""",
        (sources.vehicle_batch_id,),
    ).fetchone()
    service_source=conn.execute(
        """SELECT organization_id,location_id,source_sha256,row_count
           FROM auto_service_import_batch WHERE id=%s""",
        (sources.service_batch_id,),
    ).fetchone()
    inventory_source=conn.execute(
        """SELECT organization_id,source_sha256,row_count
           FROM auto_migration_staging_batch WHERE id=%s""",
        (sources.inventory_batch_id,),
    ).fetchone()
    if (vehicle_source is None or service_source is None
        or inventory_source is None
        or vehicle_source[:2]!=(org,sources.vehicle_sha256)
        or service_source[:3]!=(org,loc,sources.service_sha256)
        or inventory_source[:2]!=(org,sources.inventory_sha256)):
        raise SyntheticCutoverDenied("SOURCE_MISMATCH")
    vehicle = conn.execute(
        """SELECT v.vin,v.make,v.model,line.vin,line.make,line.model,
                  line.organization_id,line.location_id
           FROM auto_vehicle_migration_promotion p
           JOIN auto_vehicle_migration_line line
             ON line.batch_id=p.batch_id AND line.row_number=p.row_number
           JOIN auto_vehicle v ON v.id=p.vehicle_id
           WHERE p.batch_id=%s ORDER BY p.row_number""",
        (sources.vehicle_batch_id,),
    ).fetchall()
    count_vehicle = conn.execute(
        """SELECT row_count FROM auto_vehicle_migration_batch
           WHERE id=%s AND organization_id=%s""",
        (sources.vehicle_batch_id,org),
    ).fetchone()
    services = conn.execute(
        """SELECT line.code,line.title,line.duration_minutes,
                  cat.code,cat.title,cat.duration_minutes,
                  cat.organization_id,cat.location_id
           FROM auto_service_import_activation a
           JOIN auto_service_import_line line
             ON line.batch_id=a.batch_id AND line.row_number=a.row_number
           JOIN auto_service_catalog cat ON cat.id=a.service_id
           WHERE a.batch_id=%s ORDER BY a.row_number""",
        (sources.service_batch_id,),
    ).fetchall()
    count_services = conn.execute(
        """SELECT row_count FROM auto_service_import_batch
           WHERE id=%s AND organization_id=%s AND location_id=%s""",
        (sources.service_batch_id,org,loc),
    ).fetchone()
    if (count_vehicle is None or count_services is None
        or len(vehicle)!=count_vehicle[0] or len(services)!=count_services[0]
        or not vehicle or not services):
        raise SyntheticCutoverDenied("RECONCILIATION_FAILED")
    for row in vehicle:
        if row[:3]!=row[3:6] or row[6:]!=(org,loc):
            raise SyntheticCutoverDenied("RECONCILIATION_FAILED")
    for row in services:
        if row[:3]!=row[3:6] or row[6:]!=(org,loc):
            raise SyntheticCutoverDenied("RECONCILIATION_FAILED")
    # A staged VIN MUST NOT itself convey ownership or old customer history.
    leak = conn.execute(
        """SELECT 1 FROM auto_vehicle_migration_promotion p
           JOIN auto_vehicle_customer_link cl ON cl.vehicle_id=p.vehicle_id
           WHERE p.batch_id=%s LIMIT 1""",
        (sources.vehicle_batch_id,),
    ).fetchone()
    if leak is not None:
        raise SyntheticCutoverDenied("OWNERSHIP_UNVERIFIED")
    return {"vehicles":len(vehicle),"services":len(services)}


def rehearse_synthetic_cutover(
    conn: psycopg.Connection,token: str,*,organization_id: UUID,
    location_id: UUID,sources: SyntheticCutoverSources,
    synthetic_only: bool=False,
) -> dict:
    _validate(token,organization_id,location_id,sources,synthetic_only)
    with conn.transaction():
        # Source validation and all three promotions share a DB transaction
        # and the same locked live grants; no half-imported inventory.
        _authority(conn,token,organization_id,location_id)
        vehicle_stage=stage_authorized_synthetic_vehicles(
            conn,token,sources.vehicle_path,organization_id=organization_id,
            location_id=location_id,batch_id=sources.vehicle_batch_id,
            expected_sha256=sources.vehicle_sha256,
        )
        service_stage=stage_synthetic_services(
            conn,token,sources.service_path,organization_id=organization_id,
            location_id=location_id,batch_id=sources.service_batch_id,
            expected_sha256=sources.service_sha256,
        )
        inventory_stage=stage_synthetic_csv(
            conn,sources.inventory_path,organization_id=organization_id,
            batch_id=sources.inventory_batch_id,
            expected_sha256=sources.inventory_sha256,
        )
        # Serialized source staging + activation. If any activation fails,
        # all stages and all created SKUs/RECEIPTs roll back too.
        vehicle=promote_synthetic_vehicles(
            conn,token,organization_id=organization_id,
            location_id=location_id,batch_id=sources.vehicle_batch_id,
            synthetic_only=True,
        )
        service=activate_synthetic_services(
            conn,token,organization_id=organization_id,
            location_id=location_id,batch_id=sources.service_batch_id,
            synthetic_only=True,
        )
        inventory=activate_synthetic_inventory(
            conn,token,organization_id=organization_id,
            location_id=location_id,batch_id=sources.inventory_batch_id,
            synthetic_only=True,
        )
        _verify_source_staging(conn,organization_id,location_id,sources)
        summary=_read_promoted(conn,organization_id,location_id,sources)
        checked=reconcile_synthetic_inventory(
            conn,token,organization_id=organization_id,location_id=location_id,
            batch_id=sources.inventory_batch_id,synthetic_only=True,
        )
        if (summary != {"vehicles":vehicle["count"],"services":service["count"]}
            or checked["rows"]!=inventory["rows"]
            or checked["skus"]!=inventory["skus"]
            or checked["receipts"]!=inventory["receipts"]):
            raise SyntheticCutoverDenied("RECONCILIATION_FAILED")
        return {"ok":True,"mode":"SYNTHETIC_ATOMIC_CUTOVER",
                "replayed":all(result["replayed"] for result in (
                    vehicle_stage,service_stage,inventory_stage,
                    vehicle,service,inventory)),
                "vehicles":summary["vehicles"],"services":summary["services"],
                "stockRows":inventory["rows"],"skus":inventory["skus"],
                "receipts":inventory["receipts"],
                "writes":sum(result["writes"] for result in (
                    vehicle_stage,service_stage,inventory_stage,
                    vehicle,service,inventory)),
                "productionApproved":False}


def reconcile_synthetic_cutover(
    conn: psycopg.Connection,token: str,*,organization_id: UUID,
    location_id: UUID,sources: SyntheticCutoverSources,
    synthetic_only: bool=False,
) -> dict:
    """Independent read-only reconciliation; no staging/promotion calls."""
    _validate(token,organization_id,location_id,sources,synthetic_only)
    with conn.transaction():
        _authority(conn,token,organization_id,location_id)
        _verify_source_staging(conn,organization_id,location_id,sources)
        result=_read_promoted(conn,organization_id,location_id,sources)
        checked=reconcile_synthetic_inventory(
            conn,token,organization_id=organization_id,location_id=location_id,
            batch_id=sources.inventory_batch_id,synthetic_only=True,
        )
        return {"ok":True,"mode":"SYNTHETIC_CUTOVER_RECONCILIATION",
                **result,"stockRows":checked["rows"],"skus":checked["skus"],
                "receipts":checked["receipts"],"writes":0,
                "productionApproved":False}
