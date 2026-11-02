"""Stage fictional vehicles atomically in PG17 without linking customers or history."""
from __future__ import annotations

import csv
import hashlib
import io
import os
import stat
import tempfile
from pathlib import Path
from uuid import UUID

import psycopg

from scripts.auto_vehicle_migration_preflight import MAX_BYTES, inspect_vehicle_csv


class VehicleStagingRejected(ValueError):
    """Generic error messages must not contain vehicle or tenant identifiers."""


def stage_synthetic_vehicles(conn, path: Path, *, organization_id: UUID,
                             batch_id: UUID, expected_sha256: str,
                             required_location_id: UUID | None = None) -> dict:
    if not isinstance(organization_id, UUID) or not isinstance(batch_id, UUID):
        raise VehicleStagingRejected("invalid scope")
    if (not isinstance(expected_sha256, str) or len(expected_sha256) != 64
            or any(c not in "0123456789abcdef" for c in expected_sha256)):
        raise VehicleStagingRejected("invalid digest")
    try:
        if not hasattr(os, "O_NOFOLLOW"):
            raise VehicleStagingRejected("secure open unavailable")
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW
                     | getattr(os, "O_BINARY", 0))
        with os.fdopen(fd, "rb") as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                raise VehicleStagingRejected("source unavailable")
            snapshot = handle.read(MAX_BYTES + 1)
    except OSError:
        raise VehicleStagingRejected("source unavailable") from None
    if len(snapshot) > MAX_BYTES or hashlib.sha256(snapshot).hexdigest() != expected_sha256:
        raise VehicleStagingRejected("source unavailable")
    with tempfile.TemporaryDirectory(prefix="synthetic-vehicles-") as folder:
        copy = Path(folder) / "vehicles.csv"
        copy.write_bytes(snapshot)
        result = inspect_vehicle_csv(copy)
    if not result["ok"]:
        raise VehicleStagingRejected("vehicle preflight rejected")
    records = list(csv.DictReader(io.StringIO(snapshot.decode("utf-8-sig"), newline="")))
    if any(UUID(row["organization_id"]) != organization_id for row in records):
        raise VehicleStagingRejected("tenant mismatch")
    locations = sorted({UUID(row["location_id"]) for row in records})
    if required_location_id is not None and (type(required_location_id) is not UUID
            or locations != [required_location_id]):
        raise VehicleStagingRejected("branch mismatch")
    with conn.transaction():
        for location_id in locations:
            if conn.execute("""
                SELECT 1 FROM auto_service_location l
                JOIN auto_organization o ON o.id=l.organization_id
                WHERE l.id=%s AND o.id=%s
                AND l.disabled_at IS NULL AND o.disabled_at IS NULL
                FOR SHARE OF l,o
            """, (location_id, organization_id)).fetchone() is None:
                raise VehicleStagingRejected("scope unavailable")
        # The current auto_vehicle table has globally unique VINs. Prevent
        # staging a synthetic candidate that already belongs to an existing
        # vehicle. Never expose the matching VIN or any owner/history fields.
        if conn.execute("""
            SELECT 1 FROM auto_vehicle
            WHERE vin = ANY(%s::text[]) LIMIT 1
        """, ([row["vin"] for row in records],)).fetchone() is not None:
            raise VehicleStagingRejected("vehicle identity conflicts with existing data")
        conn.execute("""
            INSERT INTO auto_vehicle_migration_batch(id,organization_id,source_sha256,row_count)
            VALUES(%s,%s,%s,%s) ON CONFLICT(id) DO NOTHING
        """, (batch_id, organization_id, expected_sha256, len(records)))
        batch = conn.execute("""
            SELECT organization_id,source_sha256,row_count
            FROM auto_vehicle_migration_batch WHERE id=%s FOR UPDATE
        """, (batch_id,)).fetchone()
        if batch != (organization_id, expected_sha256, len(records)):
            raise VehicleStagingRejected("batch mismatch")
        stored = conn.execute("""
            SELECT row_number,organization_id,location_id,source_id,source_system,
                   vin,make,model
            FROM auto_vehicle_migration_line WHERE batch_id=%s ORDER BY row_number
        """, (batch_id,)).fetchall()
        if stored:
            if len(stored) != len(records):
                raise VehicleStagingRejected("incomplete batch")
            for row_number, (original, row) in enumerate(zip(stored, records), start=2):
                expected = (row_number, organization_id, UUID(row["location_id"]),
                            row["source_id"], row["source_system"], row["vin"],
                            row["make"], row["model"])
                if original != expected:
                    raise VehicleStagingRejected("line mismatch")
            return {"ok": True, "replayed": True, "count": len(records),
                    "writes": 0, "mode": "SYNTHETIC_VEHICLE_STAGING"}
        # Savepoint protects the outer transaction from PostgreSQL unique errors.
        # Do not expose PostgreSQL's DETAIL field (which includes the VIN).
        try:
            with conn.transaction():
            for row_number, row in enumerate(records, start=2):
                conn.execute("""
                    INSERT INTO auto_vehicle_migration_line
                    (batch_id,row_number,organization_id,location_id,source_id,
                     source_system,vin,make,model)
                    VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """, (batch_id, row_number, organization_id, UUID(row["location_id"]),
                      row["source_id"], row["source_system"], row["vin"],
                      row["make"], row["model"]))
            except psycopg.errors.UniqueViolation:
            raise VehicleStagingRejected("vehicle staging identity conflict") from None
        if conn.execute("""
            SELECT count(*) FROM auto_vehicle_migration_line WHERE batch_id=%s
        """, (batch_id,)).fetchone()[0] != len(records):
            raise VehicleStagingRejected("reconciliation mismatch")
    return {"ok": True, "replayed": False, "count": len(records),
            "writes": len(records) + 1, "mode": "SYNTHETIC_VEHICLE_STAGING"}
