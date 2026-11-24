"""PG17 warehouse reconciliation stays consistent across paired lot movement."""
from uuid import uuid4
import psycopg
from roboport_api.auto_stock_lot_work_order_issue import issue_work_order_from_lot
from roboport_api.auto_stock_lot_work_order_return import return_work_order_to_lot
from test_pg_baseline import DSN
from test_pg_auto_vehicle_claim_http import _scenario
from test_pg_auto_stock_lot_work_order_issue import setup


def snapshot(db,sku):
    return db.execute("""SELECT on_hand,reserved,lot_allocated,unallocated,
      available,discrepancy FROM auto_stock_reconciliation WHERE sku_id=%s""",
      (sku,)).fetchone()


def test_reconciliation_through_issue_and_return():
    d=_scenario()
    with psycopg.connect(DSN) as db:
        org,loc,order,item,lot,_=setup(db,d,3)
        assert snapshot(db,item)==(3,3,3,0,0,False)
        issue_work_order_from_lot(db,d["tokens"][d["intake"]],
            org,loc,order,lot,uuid4(),2)
        assert snapshot(db,item)==(1,1,1,0,0,False)
        return_work_order_to_lot(db,d["tokens"][d["intake"]],
            org,loc,order,lot,uuid4(),1)
        assert snapshot(db,item)==(2,1,2,0,1,False)


def test_reconciliation_tenant_scope_is_explicit():
    d=_scenario()
    with psycopg.connect(DSN) as db:
        org,loc,order,item,lot,_=setup(db,d,1)
        row=db.execute("""SELECT organization_id,location_id,article
          FROM auto_stock_reconciliation WHERE sku_id=%s""",(item,)).fetchone()
        assert row[0]==org and row[1]==loc and row[2]
        assert db.execute("""SELECT count(*) FROM auto_stock_reconciliation
          WHERE organization_id=%s AND location_id=%s AND sku_id=%s""",
          (uuid4(),loc,item)).fetchone()[0]==0


def test_http_stock_reconciliation_full_lifecycle_and_scope(monkeypatch):
    from fastapi.testclient import TestClient
    from roboport_api.main import app
    from test_pg_auto_stock_service import grant
    from test_pg_auto_stock_ledger import sku,record
    from test_pg_auto_stock_bin_lot import add_bin,add_lot
    from test_pg_auto_bay_booking import _org_and_bay

    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTO_API","1")
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTH_API","1")
    monkeypatch.setenv("ROBOport_DATABASE_URL",DSN)
    d=_scenario()
    with psycopg.connect(DSN) as db:
        org,loc,order,item,lot,warehouse_grant=setup(db,d,3)
        article=db.execute("SELECT article FROM auto_stock_sku WHERE id=%s",(item,)).fetchone()[0]
        issue_work_order_from_lot(db,d["tokens"][d["intake"]],org,loc,order,lot,uuid4(),2)
        return_work_order_to_lot(db,d["tokens"][d["intake"]],org,loc,order,lot,uuid4(),1)
        manager_grant=grant(db,d["manager"],org,loc,"MANAGER")
        other,otherloc,_=_org_and_bay(db)
        foreign=sku(db,other,otherloc)
        # 52 SKU в существующем филиале и 52 партии исходного SKU.
        bin_id=add_bin(db,org,loc,"EXTRA-RECON")
        lot_ids={str(lot)}
        for i in range(51):
            sku(db,org,loc)
            lot_ids.add(str(add_lot(db,org,loc,item,bin_id,f"RECON-{i:03}")))
        # Увеличиваем число исходных движений, не изменяя резерв исходного наряда.
        for i in range(26):
            record(db,item,"RESERVE",1)
            record(db,item,"RELEASE",1)
    base=f"/api/v1/dev/auto/organizations/{org}/warehouse/reconciliation"
    params={"locationId":str(loc)}
    with TestClient(app,base_url="https://127.0.0.1") as client:
        assert client.get(base,params=params).status_code==403
        client.cookies.set("rp_session",d["tokens"][d["intake"]])
        initial=client.get(base,params=params)
        assert initial.status_code==200,initial.text
        assert initial.headers["cache-control"]=="no-store"
        first=initial.json()
        assert first["summary"]=={"total":52,"discrepancies":0,"unallocated":0,
            "reserved":1,"outOfStock":51,"filtered":52}
        assert len(first["items"])==50 and first["nextCursor"]
        second=client.get(base,params={**params,"cursor":first["nextCursor"]}).json()
        assert len(second["items"])==2 and second["nextCursor"] is None
        assert len({x["skuId"] for x in first["items"]+second["items"]})==52
        row=next(x for x in first["items"]+second["items"] if x["skuId"]==str(item))
        assert (row["onHand"],row["reserved"],row["allocated"],row["unallocated"],row["available"],row["discrepancy"])==("2","1","2","0","1",False)
        filtered=client.get(base,params={**params,"status":"RESERVED"}).json()
        assert filtered["summary"]["filtered"]==1 and filtered["items"]==[row]
        assert client.get(base,params={**params,"status":"DISCREPANCY"}).json()["items"]==[]
        assert client.get(base,params={**params,"q":article}).json()["items"]==[row]
        assert client.get(base,params={**params,"status":"RESERVED","cursor":first["nextCursor"]}).status_code==422
        assert client.get(base,params={**params,"cursor":"broken"}).status_code==422
        assert client.get(base,params={**params,"locationId":str(otherloc)}).status_code==403
        assert client.get(base+"/"+str(foreign),params=params).status_code==403
        detail=base+"/"+str(item)
        lots=client.get(detail,params=params).json()
        assert len(lots["items"])==50 and lots["nextCursor"]
        tail=client.get(detail,params={**params,"cursor":lots["nextCursor"]}).json()
        assert {x["lotId"] for x in lots["items"]+tail["items"]}==lot_ids
        assert tail["nextCursor"] is None
        events=client.get(detail,params={**params,"kind":"events"}).json()
        assert len(events["items"])==50 and events["nextCursor"]
        event_tail=client.get(detail,params={**params,"kind":"events","cursor":events["nextCursor"]}).json()
        assert event_tail["nextCursor"] is None
        all_events=events["items"]+event_tail["items"]
        assert len({x["eventId"] for x in all_events})==len(all_events)
        assert {"ISSUE","RETURN","RECEIPT","RESERVE","RELEASE"}<={x["action"] for x in all_events}
        assert client.get(detail,params={**params,"kind":"events","cursor":lots["nextCursor"]}).status_code==422
        client.cookies.set("rp_session",d["tokens"][d["manager"]])
        assert client.get(base,params=params).status_code==200
        assert client.get(base,params={**params,"cursor":first["nextCursor"]}).status_code==422
        with psycopg.connect(DSN) as db:
            db.execute("UPDATE auto_organization_role_grant SET revoked_at=now() WHERE id IN (%s,%s)",
                       (warehouse_grant,manager_grant))
        for actor in (d["intake"],d["manager"]):
            client.cookies.set("rp_session",d["tokens"][actor])
            assert client.get(base,params=params).status_code==403
            assert client.get(detail,params={**params,"kind":"events"}).status_code==403
