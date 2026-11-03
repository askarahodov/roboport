"""CI-only PostgreSQL 17 restore proof for the *live synthetic* auto lifecycle.

seed: prepare one authorized automotive booking -> WorkOrder -> catalog service
      -> two material RESERVEs -> reschedule -> ISSUE/RETURN -> financial entry.
verify: independently hash the scoped relational graph in the original and
        restored databases. On the restored clone, also challenge immutable
        and scope guards in nested, rolled-back transactions.

Requires disposable CI PostgreSQL, never accepts real VIN/PII or production DSNs.
The private manifest is kept outside the dump and is deliberately never logged.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID, uuid4

import psycopg


SOURCE_DB = "roboport"
RESTORED_DB = "roboport_restored"
MANIFEST_VERSION = 1
FIELDS = (
    "organization", "location", "bay", "appointment", "booking",
    "currentBooking", "workOrder", "partSku", "consumableSku", "service",
)


def _dsn(database: str) -> str:
    raw = os.environ.get("ROBOport_DATABASE_URL", "")
    if not raw.startswith(("postgresql+psycopg://", "postgresql://")):
        raise ValueError("CI PostgreSQL DSN unavailable")
    raw = raw.replace("postgresql+psycopg://", "postgresql://", 1)
    parts = urlsplit(raw)
    # Only the two local disposable CI databases; never a remote restore.
    if (database not in (SOURCE_DB, RESTORED_DB)
        or parts.hostname not in ("127.0.0.1", "localhost")
        or parts.port != 5432 or parts.path != "/roboport"):
        raise ValueError("not a disposable loopback CI PostgreSQL DSN")
    return urlunsplit(parts._replace(path="/" + database))


def _digest(snapshot: dict) -> str:
    return hashlib.sha256(
        json.dumps(snapshot, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _rows(db: psycopg.Connection, query: str, args: tuple) -> list[list[str]]:
    return [[str(item) if item is not None else "NULL" for item in row]
            for row in db.execute(query, args).fetchall()]


def _graph(db: psycopg.Connection, ids: dict) -> dict:
    org, loc, bay, appointment, booking, current, order, part, consumable, service = (
        UUID(ids[name]) for name in FIELDS
    )
    data = {
        "identity": _rows(db, """
            SELECT a.id,a.organization_id,a.location_id,a.booking_id,
                   w.id,w.appointment_id,w.organization_id,w.location_id,
                   a.customer_link_id,a.vehicle_id
            FROM auto_appointment a JOIN auto_work_order w ON w.appointment_id=a.id
            WHERE a.id=%s AND w.id=%s AND a.organization_id=%s
                  AND a.location_id=%s
        """, (appointment, order, org, loc)),
        "vehicleScope": _rows(db, """
            SELECT count(*)
            FROM auto_appointment a
            JOIN auto_vehicle_customer_link link ON link.id=a.customer_link_id
                AND link.vehicle_id=a.vehicle_id
            JOIN auto_vehicle_location_binding binding
                ON binding.vehicle_id=a.vehicle_id AND binding.location_id=a.location_id
            WHERE a.id=%s AND a.organization_id=%s
                  AND link.verified_at IS NOT NULL AND link.revoked_at IS NULL
        """, (appointment, org)),
        "bookings": _rows(db, """
            SELECT id,organization_id,location_id,bay_id,
                   starts_at,ends_at,canceled_at IS NULL
            FROM auto_bay_booking WHERE id=ANY(%s::uuid[]) ORDER BY id
        """, ([booking, current],)),
        "moves": _rows(db, """
            SELECT appointment_id,organization_id,old_booking_id,
                   new_booking_id,actor_id
            FROM auto_appointment_reschedule
            WHERE appointment_id=%s ORDER BY id
        """, (appointment,)),
        "services": _rows(db, """
            SELECT ws.work_order_id,ws.service_id,ws.organization_id,
                   ws.location_id,c.code,c.duration_minutes
            FROM auto_work_order_service ws
            JOIN auto_service_catalog c ON c.id=ws.service_id
            WHERE ws.work_order_id=%s ORDER BY ws.service_id
        """, (order,)),
        "materials": _rows(db, """
            SELECT m.stock_event_id,m.work_order_id,m.sku_id,
                   m.item_kind,m.quantity,e.action,e.quantity
            FROM auto_appointment_material_selection m
            JOIN auto_work_order_stock_event e
              ON e.stock_event_id=m.stock_event_id
            WHERE m.work_order_id=%s ORDER BY m.sku_id
        """, (order,)),
        "stock": _rows(db, """
            SELECT e.stock_event_id,e.work_order_id,e.sku_id,e.action,
                   e.quantity,s.id,s.organization_id,s.location_id
            FROM auto_work_order_stock_event e
            JOIN auto_stock_sku s ON s.id=e.sku_id
            WHERE e.work_order_id=%s ORDER BY e.sku_id,e.created_at,e.stock_event_id
        """, (order,)),
        "balance": _rows(db, """
            SELECT b.sku_id,b.on_hand,b.reserved
            FROM auto_stock_balance b
            WHERE b.sku_id=ANY(%s::uuid[]) ORDER BY b.sku_id
        """, ([part, consumable],)),
        "stockHistory": _rows(db, """
            SELECT id,sku_id,organization_id,location_id,action,quantity
            FROM auto_stock_event
            WHERE sku_id=ANY(%s::uuid[]) ORDER BY sku_id,created_at,id
        """, ([part,consumable],)),
        "financial": _rows(db, """
            SELECT id,work_order_id,action,amount_minor
            FROM auto_work_order_financial_event
            WHERE work_order_id=%s ORDER BY created_at,id
        """, (order,)),
        "financialBalance": _rows(db, """
            SELECT work_order_id,balance_minor
            FROM auto_work_order_financial_balance WHERE work_order_id=%s
        """, (order,)),
        "appointmentEvents": _rows(db, """
            SELECT appointment_id,action FROM auto_appointment_event
            WHERE appointment_id=%s ORDER BY created_at,id
        """, (appointment,)),
        "outbox": _rows(db, """
            SELECT event_key,aggregate_type,aggregate_id,event_type,payload::text
            FROM notification_outbox
            WHERE aggregate_type='auto_appointment' AND aggregate_id=%s
            ORDER BY CASE event_type WHEN 'CREATED' THEN 0
                     WHEN 'RESCHEDULED' THEN 1 ELSE 2 END,id
        """, (appointment,)),
    }
    # Check actual relational graph, not just a matching checksum of empty data.
    if (len(data["identity"]) != 1 or len(data["vehicleScope"]) != 1
        or data["vehicleScope"][0][0] != "1"
        or len(data["bookings"]) != 2 or len(data["moves"]) != 1
        or len(data["services"]) != 1 or len(data["materials"]) != 2
        or len(data["stock"]) != 4 or len(data["stockHistory"]) != 6
        or len(data["balance"]) != 2
        or len(data["financial"]) != 1 or len(data["financialBalance"]) != 1
        or len(data["appointmentEvents"]) != 1 or len(data["outbox"]) != 2):
        raise ValueError("incomplete live auto restore graph")
    if (data["identity"][0][0] != str(appointment)
        or data["identity"][0][3] != str(booking)
        or data["identity"][0][4] != str(order)
        or data["bookings"][0][6] == data["bookings"][1][6]
        or data["moves"][0][2:4] != [str(booking),str(current)]
        or data["services"][0][1] != str(service)
        or data["financialBalance"][0][1] != "1200"
        or data["outbox"][0][3] != "CREATED"
        or data["outbox"][1][3] != "RESCHEDULED"):
        raise ValueError("invalid live auto lifecycle state")
    material_quantities = {(row[2],row[3],row[4],row[5],row[6])
                           for row in data["materials"]}
    if material_quantities != {
        (str(part),"PART","3","RESERVE","3"),
        (str(consumable),"CONSUMABLE","2","RESERVE","2"),
    }:
        raise ValueError("material classifications do not match RESERVE")
    balances = {row[0]:(row[1],row[2]) for row in data["balance"]}
    if balances != {str(part):("8","3"),str(consumable):("5","2")}:
        raise ValueError("restored stock balance differs from reserved ledger")
    actions: dict[str,list[str]] = {str(part):[],str(consumable):[]}
    for row in data["stock"]:
        if (row[6] != str(org) or row[7] != str(loc)
            or row[2] not in actions):
            raise ValueError("stock tenant/location mismatch")
        actions[row[2]].append(row[3])
    if (sorted(actions[str(part)]) != ["ISSUE","RESERVE","RETURN"]
        or actions[str(consumable)] != ["RESERVE"]):
        raise ValueError("stock ISSUE/RETURN/reserve history mismatch")
    return data


def seed(manifest_path: Path) -> None:
    # This tool MUST NOT be callable against any other database.
    source = _dsn(SOURCE_DB)
    if manifest_path.exists():
        raise ValueError("manifest already exists")
    # Reuse the real PG17 integration fixtures and real authorization service.
    import pytest
    from test_pg_auto_customer_booking_http import _setup
    from test_pg_auto_stock_service import grant
    from test_pg_auto_stock_ledger import sku, record
    from roboport_api.auto_appointment_service import (
        create_appointment, reschedule_appointment,
    )
    from roboport_api.auto_work_order_stock import mutate_work_order_stock
    from roboport_api.auto_work_order_return import return_work_order_stock

    patch = pytest.MonkeyPatch()
    try:
        d,org,loc,bay,_,_,http = _setup(patch)
        token = d["tokens"][d["intake"]]
        with psycopg.connect(source) as db:
            grant(db,d["intake"],org,loc,"INTAKE")
            grant(db,d["intake"],org,loc,"WAREHOUSE")
            part,consumable=sku(db,org,loc),sku(db,org,loc)
            record(db,part,"RECEIPT",8)
            record(db,consumable,"RECEIPT",5)
            service=uuid4()
            db.execute(
                """INSERT INTO auto_service_catalog
                   (id,organization_id,location_id,code,title,duration_minutes)
                   VALUES(%s,%s,%s,'SYN-RESTORE-TRACE','Synthetic restore trace',45)""",
                (service,org,loc),
            )
            starts=datetime.now(timezone.utc)+timedelta(days=43)
            ends=starts+timedelta(minutes=90)
            appointment,booking,order=create_appointment(
                db,token,org,loc,bay,d["link"],starts,ends,
                request_key=uuid4(),service_ids=(service,),
                material_items=((part,"PART",3),(consumable,"CONSUMABLE",2)),
            )
            # The synthetic CHARGE is intentionally before the move:
            # financial ledger still requires the original booking to be live.
            db.execute(
                "SELECT record_auto_work_order_financial(%s,%s,'CHARGE',1200,%s)",
                (order,uuid4(),"synthetic restore valuation only"),
            )
            moved=reschedule_appointment(
                db,token,org,appointment,bay,
                starts+timedelta(hours=3),ends+timedelta(hours=3),
                request_key=uuid4(),
            )
            mutate_work_order_stock(
                db,token,org,loc,order,part,uuid4(),"ISSUE",1,
            )
            return_work_order_stock(
                db,token,org,loc,order,part,uuid4(),1,
            )
            ids={
                "organization":str(org),"location":str(loc),"bay":str(bay),
                "appointment":str(appointment),"booking":str(booking),
                "currentBooking":str(moved),"workOrder":str(order),
                "partSku":str(part),"consumableSku":str(consumable),
                "service":str(service),
            }
            graph=_graph(db,ids)
        manifest={"version":MANIFEST_VERSION,"ids":ids,"sha256":_digest(graph)}
        # Manifest never enters the DB dump. Never write an unprotected path.
        fd=os.open(manifest_path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,"w",encoding="utf-8") as stream:
            json.dump(manifest,stream,sort_keys=True)
    finally:
        patch.undo()
    print("SYNTHETIC_AUTO_RESTORE_SEED_OK")


def _load(path: Path) -> dict:
    raw=json.loads(path.read_text(encoding="utf-8"))
    if (type(raw) is not dict or set(raw)!={"version","ids","sha256"}
        or raw["version"]!=MANIFEST_VERSION
        or type(raw["ids"]) is not dict or set(raw["ids"])!=set(FIELDS)
        or type(raw["sha256"]) is not str or len(raw["sha256"])!=64
        or any(type(raw["ids"][k]) is not str for k in FIELDS)):
        raise ValueError("invalid private restore manifest")
    for item in raw["ids"].values():
        UUID(item)
    return raw


def verify(manifest_path: Path,database: str,negative: bool=False) -> None:
    ids=_load(manifest_path)
    with psycopg.connect(_dsn(database)) as db:
        graph=_graph(db,ids["ids"])
        if _digest(graph)!=ids["sha256"]:
            raise ValueError("live automotive lifecycle graph checksum mismatch")
        if negative:
            if database!=RESTORED_DB:
                raise ValueError("mutating guard probes allowed only on clone")
            _challenge_guards(db,ids["ids"])
            # The failed operations must leave the graph exactly unchanged.
            if _digest(_graph(db,ids["ids"]))!=ids["sha256"]:
                raise ValueError("mutation guard probe altered restored data")
    print("SYNTHETIC_AUTO_RESTORE_VERIFY_OK")


def _challenge_guards(db: psycopg.Connection, ids: dict) -> None:
    order=UUID(ids["workOrder"])
    current=UUID(ids["currentBooking"])
    original=UUID(ids["booking"])
    sku=UUID(ids["partSku"])
    org=UUID(ids["organization"])
    loc=UUID(ids["location"])
    bay=UUID(ids["bay"])
    from psycopg import errors

    # Each attempt runs under a savepoint. Failing trigger must not poison
    # the remainder of the cloned database transaction.
    def must_reject(statement: str,args: tuple,expected: type[Exception]):
        try:
            with db.transaction():
                db.execute(statement,args)
        except expected:
            return
        raise ValueError("restored schema accepted forbidden mutation")

    must_reject(
        "UPDATE auto_appointment_material_selection SET quantity=1 WHERE work_order_id=%s",
        (order,),errors.RaiseException,
    )
    must_reject(
        "UPDATE auto_work_order_stock_event SET quantity=1 WHERE work_order_id=%s",
        (order,),errors.RaiseException,
    )
    must_reject(
        "UPDATE auto_appointment_reschedule SET actor_id=actor_id WHERE new_booking_id=%s",
        (current,),errors.RaiseException,
    )
    must_reject(
        "UPDATE auto_bay_booking SET canceled_at=now() WHERE id=%s",
        (current,),errors.RaiseException,
    )
    must_reject(
        """INSERT INTO auto_bay_booking
           (id,organization_id,location_id,bay_id,starts_at,ends_at)
           SELECT %s,%s,%s,%s,starts_at,ends_at
           FROM auto_bay_booking WHERE id=%s""",
        (uuid4(),org,loc,bay,current),errors.ExclusionViolation,
    )
    must_reject(
        """INSERT INTO auto_stock_event
           (id,request_id,sku_id,organization_id,location_id,action,quantity)
           VALUES(%s,%s,%s,%s,%s,'RECEIPT',1)""",
        (uuid4(),uuid4(),sku,uuid4(),loc),errors.RaiseException,
    )
    # A restored database with accidentally removed immutable triggers
    # fails this probe; no successful writes are committed.


def main(argv: list[str]|None=None) -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode",choices=("seed","verify","verify-clone"))
    parser.add_argument("--manifest",type=Path,required=True)
    parser.add_argument("--database",choices=(SOURCE_DB,RESTORED_DB),
                        default=SOURCE_DB)
    args=parser.parse_args(argv)
    if args.mode=="seed" and args.database!=SOURCE_DB:
        parser.error("seed only allowed on original disposable CI DB")
    if args.mode=="verify-clone" and args.database!=RESTORED_DB:
        parser.error("negative mutations only allowed on restored clone")
    try:
        if args.mode=="seed":
            seed(args.manifest)
        else:
            verify(args.manifest,args.database,negative=args.mode=="verify-clone")
    except (OSError,ValueError,psycopg.Error) as exc:
        print("SYNTHETIC_AUTO_RESTORE_BLOCKED:"+type(exc).__name__,file=sys.stderr)
        return 1
    return 0


if __name__=="__main__":
    raise SystemExit(main())
