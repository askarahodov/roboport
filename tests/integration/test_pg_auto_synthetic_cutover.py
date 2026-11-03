"""PG17 end-to-end, all-or-nothing synthetic migration cutover.

Three different files -> verified staging -> vehicle/service/inventory activation
-> immutable provenance and stock balance. Neither VIN nor legacy history
automatically becomes customer ownership. No real PII or production data.
"""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
import csv
from threading import Barrier
from uuid import UUID,uuid4

import psycopg
import pytest

from roboport_api.auto_synthetic_cutover import (
    SyntheticCutoverDenied,SyntheticCutoverSources,
    rehearse_synthetic_cutover,reconcile_synthetic_cutover,
)
from roboport_api.auto_inventory_activation import (
    InventoryActivationDenied,activate_synthetic_inventory,
)
from roboport_api.auto_vehicle_promotion_service import VehiclePromotionDenied
from test_pg_auto_vehicle_claim_http import _scenario
from test_pg_auto_bay_booking import _org_and_bay
from test_pg_auto_stock_service import grant
from test_pg_auto_stock_ledger import balance,sku,record
from test_pg_baseline import DSN
from scripts.auto_vehicle_migration_preflight import COLUMNS as VEHICLE_FIELDS
from roboport_api.auto_service_import import FIELDS as SERVICE_FIELDS
from scripts.auto_migration_dry_run import COLUMNS as INVENTORY_FIELDS


def _write(path:Path,fields,rows):
    with path.open("w",newline="",encoding="utf-8") as file:
        writer=csv.writer(file)
        writer.writerow(fields)
        writer.writerows(rows)
    return sha256(path.read_bytes()).hexdigest()


def _sources(directory:Path,org:UUID,loc:UUID,*,article="FILTER-001",
             service_code="BRAKE-REPAIR",foreign_loc=None) -> SyntheticCutoverSources:
    directory.mkdir(parents=True,exist_ok=True)
    v=directory/"vehicles.csv"
    s=directory/"services.csv"
    i=directory/"inventory.csv"
    # Random hex is a fictional 17-character VIN-shaped fixture, not a
    # production VIN. No owner, consent, documents or legacy history.
    vin=uuid4().hex[:17].upper()
    vh=_write(v,VEHICLE_FIELDS,[
        ("VEHICLE","SYN-CAR-1",str(org),str(loc),vin,
         "SYNTHETIC","TEST CAR","DEMO"),
    ])
    sh=_write(s,SERVICE_FIELDS,[
        ("SERVICE","DEMO","LABOR-1",service_code,"SYNTHETIC SERVICE",60),
        ("SERVICE","DEMO","LABOR-2","CHECK-COOLANT","SYNTHETIC CHECK",30),
    ])
    ih=_write(i,INVENTORY_FIELDS,[
        ("STOCK","STOCK-1",str(org),str(loc),article,"3"),
        ("SKU","SKU-1",str(org),str(loc),article,"0"),
        ("SKU","SKU-2",str(org),str(foreign_loc or loc),"COOLANT-001","0"),
        ("STOCK","STOCK-2",str(org),str(loc),article,"0"),
        ("STOCK","STOCK-3",str(org),
         str(foreign_loc or loc),"COOLANT-001","5"),
    ])
    return SyntheticCutoverSources(
        v,vh,uuid4(),s,sh,uuid4(),i,ih,uuid4(),
    )


def _setup(tmp_path):
    d=_scenario()
    with psycopg.connect(DSN) as db:
        org,loc,bay=_org_and_bay(db)
        manager=grant(db,d["intake"],org,loc,"MANAGER")
        warehouse=grant(db,d["intake"],org,loc,"WAREHOUSE")
    sources=_sources(tmp_path,org,loc)
    return d,org,loc,bay,manager,warehouse,sources


def _go(db,d,org,loc,sources,actor=None):
    return rehearse_synthetic_cutover(
        db,d["tokens"][actor or d["intake"]],
        organization_id=org,location_id=loc,sources=sources,
        synthetic_only=True,
    )


def _catalog(db,org,loc):
    return db.execute(
        """SELECT code,duration_minutes FROM auto_service_catalog
           WHERE organization_id=%s AND location_id=%s ORDER BY code""",
        (org,loc),
    ).fetchall()


def _stock(db,org,loc):
    return db.execute(
        """SELECT sku.article,b.on_hand,b.reserved
           FROM auto_stock_sku sku
           JOIN auto_stock_balance b ON b.sku_id=sku.id
           WHERE sku.organization_id=%s AND sku.location_id=%s
           ORDER BY sku.article""",
        (org,loc),
    ).fetchall()


def _counts(db,sources):
    return tuple(db.execute(f"SELECT count(*) FROM {table} WHERE {column}=%s",
                    (value,)).fetchone()[0] for table,column,value in (
       ("auto_vehicle_migration_batch","id",sources.vehicle_batch_id),
       ("auto_vehicle_migration_promotion","batch_id",sources.vehicle_batch_id),
       ("auto_service_import_batch","id",sources.service_batch_id),
       ("auto_service_import_activation","batch_id",sources.service_batch_id),
       ("auto_migration_staging_batch","id",sources.inventory_batch_id),
       ("auto_migration_stock_activation","batch_id",sources.inventory_batch_id),
    ))


def test_full_cutover_is_atomic_reconciled_and_repeatable(tmp_path):
    d,org,loc,_,_,_,sources=_setup(tmp_path)
    token=d["tokens"][d["intake"]]
    with psycopg.connect(DSN) as db:
        result=_go(db,d,org,loc,sources)
        assert result["ok"] and not result["replayed"]
        assert result["productionApproved"] is False
        assert result["vehicles"]==1 and result["services"]==2
        assert result["stockRows"]==5 and result["skus"]==2
        assert result["receipts"]==2
        assert result["writes"]>0
        assert _counts(db,sources)==(1,1,1,2,1,5)
        assert _catalog(db,org,loc)==[
            ("BRAKE-REPAIR",60),("CHECK-COOLANT",30),
        ]
        assert _stock(db,org,loc)==[
            ("COOLANT-001",5,0),("FILTER-001",3,0),
        ]
        # Source order starts with a STOCK line; activation is ordered
        # correctly after SKU lineage exists.
        rows=db.execute(
            """SELECT a.line_number,a.record_type,a.stock_event_id,
                      e.action,e.quantity
               FROM auto_migration_stock_activation a
               LEFT JOIN auto_stock_event e ON e.id=a.stock_event_id
               WHERE a.batch_id=%s ORDER BY a.line_number""",
            (sources.inventory_batch_id,),
        ).fetchall()
        assert rows[0][1:] == ("STOCK",rows[0][2],"RECEIPT",3)
        assert rows[3][1:] == ("STOCK",None,None,None)
        assert all(r[2] is None for r in rows if r[1]=="SKU")
        assert db.execute(
            """SELECT count(*) FROM auto_vehicle_customer_link cl
               JOIN auto_vehicle_migration_promotion p
                 ON cl.vehicle_id=p.vehicle_id
               WHERE p.batch_id=%s""",(sources.vehicle_batch_id,),
        ).fetchone()[0]==0
        assert db.execute(
            """SELECT count(*) FROM auto_work_order_service ws
               JOIN auto_service_import_activation a ON a.service_id=ws.service_id
               WHERE a.batch_id=%s""",(sources.service_batch_id,),
        ).fetchone()[0]==0
        before=_counts(db,sources)
        rechecked=reconcile_synthetic_cutover(
            db,token,organization_id=org,location_id=loc,sources=sources,
            synthetic_only=True,
        )
        assert rechecked["writes"]==0
        assert rechecked["vehicles"]==1 and rechecked["services"]==2
        replay=_go(db,d,org,loc,sources)
        assert replay["replayed"] is True and replay["writes"]==0
        assert _counts(db,sources)==before
        assert _stock(db,org,loc)==[
            ("COOLANT-001",5,0),("FILTER-001",3,0),
        ]
        # A repeat that changes a source file cannot silently replay.
        sources.service_path.write_bytes(sources.service_path.read_bytes()+b"\n")
        with pytest.raises(ValueError):
            _go(db,d,org,loc,sources)
        assert _counts(db,sources)==before


def test_requires_both_live_grants_branch_scope_and_synthetic_flag(tmp_path):
    d=_scenario()
    with psycopg.connect(DSN) as db:
        org,loc,_=_org_and_bay(db)
        other,other_loc,_=_org_and_bay(db)
        sources=_sources(tmp_path,org,loc)
        token=d["tokens"][d["intake"]]
        with pytest.raises(SyntheticCutoverDenied,match="INVALID_INPUT"):
            rehearse_synthetic_cutover(
                db,token,organization_id=org,location_id=loc,sources=sources,
            )
        with pytest.raises(SyntheticCutoverDenied,match="FORBIDDEN"):
            _go(db,d,org,loc,sources)
        manager=grant(db,d["intake"],org,loc,"MANAGER")
        with pytest.raises(SyntheticCutoverDenied,match="FORBIDDEN"):
            _go(db,d,org,loc,sources)
        assert _counts(db,sources)==(0,0,0,0,0,0)
        warehouse=grant(db,d["intake"],org,loc,"WAREHOUSE")
        with pytest.raises(SyntheticCutoverDenied,match="FORBIDDEN"):
            _go(db,d,other,other_loc,sources)
        assert _counts(db,sources)==(0,0,0,0,0,0)
        assert _go(db,d,org,loc,sources)["ok"]
        db.execute(
            "UPDATE auto_organization_role_grant SET revoked_at=now() WHERE id=%s",
            (warehouse,),
        )
        with pytest.raises(SyntheticCutoverDenied,match="FORBIDDEN"):
            _go(db,d,org,loc,sources)
        with pytest.raises(SyntheticCutoverDenied,match="FORBIDDEN"):
            reconcile_synthetic_cutover(
                db,token,organization_id=org,location_id=loc,
                sources=sources,synthetic_only=True,
            )
        assert _counts(db,sources)==(1,1,1,2,1,5)


def test_service_conflict_rolls_back_all_staging_and_vehicle_promotion(tmp_path):
    d,org,loc,_,_,_,sources=_setup(tmp_path)
    with psycopg.connect(DSN) as db:
        # Independent live catalog entry makes service activation fail
        # AFTER vehicle stage/promotion; entire bundle must roll back.
        db.execute(
            """INSERT INTO auto_service_catalog(
                  id,organization_id,location_id,code,title,duration_minutes)
               VALUES(%s,%s,%s,'BRAKE-REPAIR','EXISTING CATALOG',60)""",
            (uuid4(),org,loc),
        )
        with pytest.raises(ValueError):
            _go(db,d,org,loc,sources)
        assert _counts(db,sources)==(0,0,0,0,0,0)
        assert db.execute(
            """SELECT count(*) FROM auto_vehicle WHERE vin IN
               (SELECT vin FROM auto_vehicle_migration_line WHERE batch_id=%s)""",
            (sources.vehicle_batch_id,),
        ).fetchone()[0]==0
        assert _stock(db,org,loc)==[]
        assert _catalog(db,org,loc)==[("BRAKE-REPAIR",60)]


def test_existing_stock_article_rolls_back_vehicle_and_service_promotions(tmp_path):
    d,org,loc,_,_,_,sources=_setup(tmp_path)
    with psycopg.connect(DSN) as db:
        db.execute(
            """INSERT INTO auto_stock_sku(id,organization_id,location_id,article)
               VALUES(%s,%s,%s,'FILTER-001')""",(uuid4(),org,loc),
        )
        with pytest.raises(InventoryActivationDenied,match="ARTICLE_CONFLICT"):
            _go(db,d,org,loc,sources)
        assert _counts(db,sources)==(0,0,0,0,0,0)
        assert _catalog(db,org,loc)==[]
        assert _stock(db,org,loc)==[("FILTER-001",0,0)]


def test_failed_outer_transaction_rolls_back_every_stage_and_live_entry(tmp_path):
    d,org,loc,_,_,_,sources=_setup(tmp_path)
    with psycopg.connect(DSN) as db:
        with pytest.raises(RuntimeError):
            with db.transaction():
                assert _go(db,d,org,loc,sources)["ok"]
                raise RuntimeError("synthetic rollback drill")
        assert _counts(db,sources)==(0,0,0,0,0,0)
        assert _stock(db,org,loc)==[]
        assert _catalog(db,org,loc)==[]


def test_mixed_branch_inventory_is_rejected_before_any_promotion(tmp_path):
    d,org,loc,_,_,_,_= _setup(tmp_path/"setup")
    with psycopg.connect(DSN) as db:
        second=uuid4()
        db.execute(
            """INSERT INTO auto_service_location(id,code,organization_id)
               VALUES(%s,%s,%s)""",
            (second,"LOC_"+second.hex[:10].upper(),org),
        )
        sources=_sources(tmp_path/"mixed",org,loc,foreign_loc=second)
        with pytest.raises(InventoryActivationDenied,match="BATCH_SCOPE_MISMATCH"):
            _go(db,d,org,loc,sources)
        assert _counts(db,sources)==(0,0,0,0,0,0)
        assert _catalog(db,org,loc)==[]
        assert _stock(db,org,loc)==[]


def test_database_rejects_forged_inventory_lineage_and_immutable_mutations(tmp_path):
    d,org,loc,_,_,_,sources=_setup(tmp_path)
    with psycopg.connect(DSN) as db:
        _go(db,d,org,loc,sources)
        actual=db.execute(
            """SELECT line_number,sku_id,stock_event_id
               FROM auto_migration_stock_activation WHERE batch_id=%s
                 AND record_type='STOCK' AND stock_event_id IS NOT NULL
               ORDER BY line_number LIMIT 1""",(sources.inventory_batch_id,),
        ).fetchone()
        _,foreign_loc,_=_org_and_bay(db)
        foreign_org=db.execute(
            "SELECT organization_id FROM auto_service_location WHERE id=%s",
            (foreign_loc,),
        ).fetchone()[0]
        foreign=sku(db,foreign_org,foreign_loc)
        wrong_event=record(db,foreign,"RECEIPT",3)
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute(
                    """INSERT INTO auto_migration_stock_activation
                       (batch_id,line_number,record_type,sku_id,stock_event_id,created_by)
                       VALUES(%s,%s,'STOCK',%s,%s,%s)""",
                    (sources.inventory_batch_id,actual[0],foreign,wrong_event,d["intake"]),
                )
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute(
                    """INSERT INTO auto_migration_stock_activation
                       (batch_id,line_number,record_type,sku_id,stock_event_id,created_by)
                       VALUES(%s,%s,'SKU',%s,%s,%s)""",
                    (sources.inventory_batch_id,actual[0],actual[1],actual[2],d["intake"]),
                )
        for query in (
            "UPDATE auto_migration_stock_activation SET stock_event_id=NULL WHERE batch_id=%s",
            "DELETE FROM auto_migration_stock_activation WHERE batch_id=%s",
        ):
            with pytest.raises(psycopg.errors.RaiseException):
                with db.transaction():
                    db.execute(query,(sources.inventory_batch_id,))
        assert _counts(db,sources)==(1,1,1,2,1,5)


def test_same_batch_concurrent_rehearsals_cannot_duplicate_stock_or_vins(tmp_path):
    d,org,loc,_,_,_,sources=_setup(tmp_path)
    gate=Barrier(2)
    def worker():
        with psycopg.connect(DSN) as db:
            gate.wait(timeout=20)
            return _go(db,d,org,loc,sources)
    with ThreadPoolExecutor(max_workers=2) as pool:
        a=pool.submit(worker)
        b=pool.submit(worker)
        results=[a.result(timeout=30),b.result(timeout=30)]
    assert sorted(r["replayed"] for r in results)==[False,True]
    with psycopg.connect(DSN) as db:
        assert _counts(db,sources)==(1,1,1,2,1,5)
        assert _stock(db,org,loc)==[
            ("COOLANT-001",5,0),("FILTER-001",3,0),
        ]
