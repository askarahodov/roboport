"""CI-only PostgreSQL seed/restore proof for the three-domain synthetic cutover.

The SHA-256 manifest stays outside pg_dump, and negative SQL probes execute
only against a separate restored clone. Never use real data or production.
"""
import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from uuid import UUID,uuid4

import psycopg

from scripts.auto_restore_lifecycle_probe import _dsn

ROOT="roboport"
CLONE="roboport_restored"
FIELDS=("org","loc","vehicleBatch","serviceBatch","inventoryBatch")


def rows(db,query,args):
    return [[str(x) if x is not None else "NULL" for x in row]
            for row in db.execute(query,args).fetchall()]


def graph(db,ids):
    org,loc,vehicle,service,inventory=(UUID(ids[x]) for x in FIELDS)
    data={
        "vehicleStage":rows(db,"""
            SELECT l.batch_id,l.row_number,l.organization_id,l.location_id,
                   l.source_id,l.source_system,l.vin,l.make,l.model
            FROM auto_vehicle_migration_line l WHERE l.batch_id=%s
            ORDER BY l.row_number""",(vehicle,)),
        "vehicleActive":rows(db,"""
            SELECT p.batch_id,p.row_number,p.vehicle_id,
                   v.vin,v.make,v.model,l.vin,l.make,l.model
            FROM auto_vehicle_migration_promotion p
            JOIN auto_vehicle_migration_line l ON l.batch_id=p.batch_id
              AND l.row_number=p.row_number
            JOIN auto_vehicle v ON v.id=p.vehicle_id
            WHERE p.batch_id=%s ORDER BY p.row_number""",(vehicle,)),
        "serviceStage":rows(db,"""
            SELECT l.batch_id,l.row_number,l.source_system,l.source_id,
                   l.code,l.title,l.duration_minutes
            FROM auto_service_import_line l WHERE l.batch_id=%s
            ORDER BY l.row_number""",(service,)),
        "serviceActive":rows(db,"""
            SELECT a.batch_id,a.row_number,a.service_id,
                   c.organization_id,c.location_id,c.code,c.title,
                   c.duration_minutes,l.code,l.title,l.duration_minutes
            FROM auto_service_import_activation a
            JOIN auto_service_catalog c ON c.id=a.service_id
            JOIN auto_service_import_line l ON l.batch_id=a.batch_id
              AND l.row_number=a.row_number
            WHERE a.batch_id=%s ORDER BY a.row_number""",(service,)),
        "stockStage":rows(db,"""
            SELECT l.batch_id,l.line_number,l.organization_id,l.location_id,
                   l.record_type,l.source_id,l.article,l.quantity
            FROM auto_migration_staging_line l WHERE l.batch_id=%s
            ORDER BY l.line_number""",(inventory,)),
        "stockActive":rows(db,"""
            SELECT a.batch_id,a.line_number,a.record_type,a.sku_id,
                   a.stock_event_id,a.created_by,s.organization_id,
                   s.location_id,s.article,e.sku_id,e.organization_id,
                   e.location_id,e.action,e.quantity
            FROM auto_migration_stock_activation a
            JOIN auto_stock_sku s ON s.id=a.sku_id
            LEFT JOIN auto_stock_event e ON e.id=a.stock_event_id
            WHERE a.batch_id=%s ORDER BY a.line_number""",(inventory,)),
        "balance":rows(db,"""
            SELECT s.id,s.organization_id,s.location_id,s.article,b.on_hand,b.reserved
            FROM auto_migration_stock_activation a
            JOIN auto_stock_sku s ON s.id=a.sku_id
            JOIN auto_stock_balance b ON b.sku_id=s.id
            WHERE a.batch_id=%s AND a.record_type='SKU'
            ORDER BY s.article""",(inventory,)),
        "batches":rows(db,"""
            SELECT id,organization_id,source_sha256,row_count
            FROM auto_vehicle_migration_batch WHERE id=%s""",(vehicle,))+rows(db,"""
            SELECT id,organization_id,location_id,source_sha256,row_count
            FROM auto_service_import_batch WHERE id=%s""",(service,))+rows(db,"""
            SELECT id,organization_id,source_sha256,row_count,sku_count,stock_count
            FROM auto_migration_staging_batch WHERE id=%s""",(inventory,)),
        "ownership":rows(db,"""
            SELECT count(*) FROM auto_vehicle_customer_link link
            JOIN auto_vehicle_migration_promotion p ON p.vehicle_id=link.vehicle_id
            WHERE p.batch_id=%s""",(vehicle,)),
    }
    if ({k:len(data[k]) for k in ("vehicleStage","vehicleActive","serviceStage",
          "serviceActive","stockStage","stockActive","balance","batches")} !=
        {"vehicleStage":1,"vehicleActive":1,"serviceStage":2,
         "serviceActive":2,"stockStage":5,"stockActive":5,
         "balance":2,"batches":3} or data["ownership"]!=[["0"]]):
        raise ValueError("incomplete three-domain cutover")
    if (data["batches"][0][1]!=str(org)
        or data["batches"][1][1:3]!=[str(org),str(loc)]
        or data["batches"][2][1]!=str(org)
        or data["batches"][2][3:]!=["5","2","3"]):
        raise ValueError("mismatched source batch")
    for row in data["vehicleActive"]:
        if row[3:6]!=row[6:9]:
            raise ValueError("vehicle lineage mismatch")
    for row in data["serviceActive"]:
        if row[3:5]!=[str(org),str(loc)] or row[5:8]!=row[8:11]:
            raise ValueError("service lineage mismatch")
    staged={row[1]:row for row in data["stockStage"]}
    mapped={}
    receipts=0
    for row in data["stockActive"]:
        (batch,line,kind,sku,event,actor,sku_org,sku_loc,article,
         event_sku,event_org,event_loc,action,quantity)=row
        original=staged[line]
        if (batch!=str(inventory) or
            original[2:4]!=[str(org),str(loc)] or
            (kind,article)!=(original[4],original[6]) or
            [sku_org,sku_loc]!=[str(org),str(loc)] or actor=="NULL"):
            raise ValueError("inventory scope/provenance mismatch")
        if kind=="SKU":
            if event!="NULL" or article in mapped:
                raise ValueError("invalid sku activation")
            mapped[article]=sku
        elif original[7]=="0":
            if event!="NULL":
                raise ValueError("zero quantity generated event")
        else:
            if (event=="NULL" or
                [event_sku,event_org,event_loc,action,quantity]!=
                [sku,str(org),str(loc),"RECEIPT",original[7]]):
                raise ValueError("physical receipt mismatches source")
            receipts+=1
    for row in data["stockActive"]:
        if row[2]=="STOCK" and mapped.get(row[8])!=row[3]:
            raise ValueError("stock references SKU outside the bundle")
    if (receipts!=2 or
        {x[3]:(x[4],x[5]) for x in data["balance"]}!=
         {"COOLANT-001":("5","0"),"FILTER-001":("3","0")}):
        raise ValueError("cutover inventory balance mismatch")
    return data


def digest(data):
    return sha256(json.dumps(data,sort_keys=True,separators=(",",":")).encode()).hexdigest()


def load(path):
    result=json.loads(path.read_text(encoding="utf-8"))
    if (type(result) is not dict or set(result)!={"version","ids","sha256"}
        or result["version"]!=1 or type(result["ids"]) is not dict
        or set(result["ids"])!=set(FIELDS)
        or type(result["sha256"]) is not str or len(result["sha256"])!=64
        or any(c not in "0123456789abcdef" for c in result["sha256"])
        or any(type(result["ids"][key]) is not str for key in FIELDS)):
        raise ValueError("invalid synthetic cutover restore manifest")
    for name in FIELDS:
        UUID(result["ids"][name])
    return result


def seed(path):
    if path.exists():
        raise ValueError("manifest already exists")
    from test_pg_auto_vehicle_claim_http import _scenario
    from test_pg_auto_bay_booking import _org_and_bay
    from test_pg_auto_stock_service import grant
    from test_pg_auto_synthetic_cutover import _sources
    from roboport_api.auto_synthetic_cutover import rehearse_synthetic_cutover
    dbname=_dsn(ROOT)
    d=_scenario()
    with psycopg.connect(dbname) as db:
        org,loc,_=_org_and_bay(db)
        actor=d["intake"]
        grant(db,actor,org,loc,"MANAGER")
        grant(db,actor,org,loc,"WAREHOUSE")
        with TemporaryDirectory(prefix="pg17-cutover-") as directory:
            sources=_sources(Path(directory),org,loc)
            result=rehearse_synthetic_cutover(
                db,d["tokens"][actor],organization_id=org,location_id=loc,
                sources=sources,synthetic_only=True,
            )
            if not result["ok"] or result["replayed"] or result["writes"]==0:
                raise ValueError("synthetic cutover not created")
        ids={"org":str(org),"loc":str(loc),
             "vehicleBatch":str(sources.vehicle_batch_id),
             "serviceBatch":str(sources.service_batch_id),
             "inventoryBatch":str(sources.inventory_batch_id)}
        first=graph(db,ids)
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,"w",encoding="utf-8") as stream:
        json.dump({"version":1,"ids":ids,"sha256":digest(first)},stream,sort_keys=True)
    print("SYNTHETIC_CUTOVER_RESTORE_SEED_OK")


def check_guards(db,ids):
    inventory=UUID(ids["inventoryBatch"])
    vehicle=UUID(ids["vehicleBatch"])
    service=UUID(ids["serviceBatch"])
    loc=UUID(ids["loc"])
    sku=db.execute("""
        SELECT sku_id FROM auto_migration_stock_activation
        WHERE batch_id=%s AND record_type='SKU' LIMIT 1
    """,(inventory,)).fetchone()[0]
    def reject(query,args):
        try:
            with db.transaction():
                db.execute(query,args)
        except psycopg.errors.RaiseException:
            return
        raise ValueError("restored cutover accepted forbidden mutation")
    reject("UPDATE auto_migration_stock_activation SET record_type=record_type WHERE batch_id=%s",
           (inventory,))
    reject("DELETE FROM auto_migration_stock_activation WHERE batch_id=%s",(inventory,))
    reject("UPDATE auto_vehicle_migration_promotion SET row_number=row_number WHERE batch_id=%s",
           (vehicle,))
    reject("UPDATE auto_service_import_activation SET row_number=row_number WHERE batch_id=%s",
           (service,))
    reject("""INSERT INTO auto_stock_event(
            id,request_id,sku_id,organization_id,location_id,action,quantity)
            VALUES(%s,%s,%s,%s,%s,'RECEIPT',1)""",
           (uuid4(),uuid4(),sku,uuid4(),loc))


def verify(path,database,*,clone=False):
    if clone and database!=CLONE:
        raise ValueError("negative probes must only mutate restored clone")
    manifest=load(path)
    with psycopg.connect(_dsn(database)) as db:
        before=graph(db,manifest["ids"])
        if digest(before)!=manifest["sha256"]:
            raise ValueError("restored cutover differs from pre-dump source")
        if clone:
            check_guards(db,manifest["ids"])
            if digest(graph(db,manifest["ids"]))!=manifest["sha256"]:
                raise ValueError("clone guard probes mutated the graph")
    print("SYNTHETIC_CUTOVER_RESTORE_VERIFY_OK")


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode",choices=("seed","verify","verify-clone"))
    parser.add_argument("--manifest",type=Path,required=True)
    parser.add_argument("--database",choices=(ROOT,CLONE),default=ROOT)
    args=parser.parse_args(argv)
    if args.mode=="seed" and args.database!=ROOT:
        parser.error("seed allowed only against disposable source")
    if args.mode=="verify-clone" and args.database!=CLONE:
        parser.error("negative restore probes require clone")
    try:
        if args.mode=="seed":
            seed(args.manifest)
        else:
            verify(args.manifest,args.database,clone=args.mode=="verify-clone")
    except (OSError,ValueError,psycopg.Error) as exc:
        print("SYNTHETIC_CUTOVER_RESTORE_BLOCKED:"+type(exc).__name__,file=sys.stderr)
        return 1
    return 0


if __name__=="__main__":
    raise SystemExit(main())
