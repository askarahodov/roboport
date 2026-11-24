"""PG17 auditable supplier return / write-off of warehouse parts."""
from uuid import uuid4
import psycopg
import pytest

from roboport_api.auto_stock_disposition import dispose_stock_lot
from roboport_api.auto_stock_service import AutoStockDenied,AutoStockConflict
from test_pg_baseline import DSN
from test_pg_auto_vehicle_claim_http import _scenario
from test_pg_auto_stock_lot_allocation import setup,allocated,act
from test_pg_auto_stock_ledger import record,balance
from test_pg_auto_bay_booking import _org_and_bay


def dispose(db,d,org,loc,lot,action,qty,key=None,actor=None,ref=None):
    return dispose_stock_lot(db,d["tokens"][actor or d["intake"]],
                            org,loc,lot,key or uuid4(),action,qty,
                            "physical disposition",ref)


def test_writeoff_and_supplier_return_consume_physical_and_lot_balances():
    d=_scenario()
    with psycopg.connect(DSN) as db:
        org,loc,item,lot,_=setup(db,d,6)
        act(db,d,org,loc,lot,"ALLOCATE",6)
        key=uuid4()
        ident=dispose(db,d,org,loc,lot,"WRITE_OFF",2,key)
        assert dispose(db,d,org,loc,lot,"WRITE_OFF",2,key)==ident
        assert allocated(db,lot)==4 and balance(db,item)==(4,0)
        with pytest.raises(AutoStockConflict):
            dispose(db,d,org,loc,lot,"WRITE_OFF",3,key)
        returned=dispose(db,d,org,loc,lot,"SUPPLIER_RETURN",3,ref="SUP-CASE-1")
        assert allocated(db,lot)==1 and balance(db,item)==(1,0)
        row=db.execute("""SELECT de.action,a.action,d.action,d.supplier_reference
          FROM auto_stock_lot_disposition d
          JOIN auto_stock_event de ON de.id=d.stock_event_id
          JOIN auto_stock_lot_allocation a ON a.id=d.lot_debit_id
          WHERE d.id=%s""",(returned,)).fetchone()
        assert row==("SUPPLIER_RETURN","DEALLOCATE","SUPPLIER_RETURN","SUP-CASE-1")
        with pytest.raises(AutoStockConflict):
            dispose(db,d,org,loc,lot,"SUPPLIER_RETURN",2,ref="SUP-CASE-1")
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("DELETE FROM auto_stock_lot_disposition WHERE id=%s",(ident,))


def test_reserved_units_cannot_be_written_off_and_transaction_rollback():
    d=_scenario()
    with psycopg.connect(DSN) as db:
        org,loc,item,lot,_=setup(db,d,4)
        act(db,d,org,loc,lot,"ALLOCATE",4)
        record(db,item,"RESERVE",3)
        with pytest.raises(AutoStockConflict):
            dispose(db,d,org,loc,lot,"WRITE_OFF",2)
        assert balance(db,item)==(4,3) and allocated(db,lot)==4
        with pytest.raises(RuntimeError):
            with db.transaction():
                dispose(db,d,org,loc,lot,"WRITE_OFF",1)
                raise RuntimeError("rollback")
        assert balance(db,item)==(4,3) and allocated(db,lot)==4


def test_wrong_tenant_role_and_revocation():
    d=_scenario()
    with psycopg.connect(DSN) as db:
        org,loc,item,lot,role=setup(db,d,2)
        act(db,d,org,loc,lot,"ALLOCATE",2)
        other,foreign,_=_org_and_bay(db)
        with pytest.raises(AutoStockDenied):
            dispose(db,d,other,foreign,lot,"WRITE_OFF",1)
        with pytest.raises(AutoStockDenied):
            dispose(db,d,org,loc,lot,"WRITE_OFF",1,actor=d["manager"])
        key=uuid4()
        dispose(db,d,org,loc,lot,"WRITE_OFF",1,key)
        db.execute("UPDATE auto_organization_role_grant SET revoked_at=now() WHERE id=%s",(role,))
        with pytest.raises(AutoStockDenied):
            dispose(db,d,org,loc,lot,"WRITE_OFF",1,key)


def test_concurrent_disposition_serializes_on_sku():
    d=_scenario()
    with psycopg.connect(DSN) as db:
        org,loc,item,lot,_=setup(db,d,1)
        act(db,d,org,loc,lot,"ALLOCATE",1)
    with psycopg.connect(DSN) as first,psycopg.connect(DSN) as second:
        with first.transaction():
            dispose(first,d,org,loc,lot,"WRITE_OFF",1)
            second.execute("SET lock_timeout='200ms'")
            with pytest.raises(psycopg.errors.LockNotAvailable):
                with second.transaction():
                    dispose(second,d,org,loc,lot,"WRITE_OFF",1)
        with pytest.raises(AutoStockConflict):
            dispose(second,d,org,loc,lot,"WRITE_OFF",1)
        assert balance(second,item)==(0,0) and allocated(second,lot)==0


def test_document_writeoff_complete_lifecycle_http(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from fastapi.testclient import TestClient
    from roboport_api.main import app
    from roboport_api.auto_stock_writeoff import decide
    from test_pg_auto_stock_reclassification_http import post
    from test_pg_auto_stock_service import grant
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTO_API","1")
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTH_API","1")
    monkeypatch.setenv("ROBOport_DATABASE_URL",DSN)
    d=_scenario()
    with psycopg.connect(DSN) as db:
        org,loc,item,lot,warehouse_grant=setup(db,d,8)
        act(db,d,org,loc,lot,"ALLOCATE",8)
        record(db,item,"RESERVE",3)
        manager_grant=grant(db,d["manager"],org,loc,"MANAGER")
        grant(db,d["intake"],org,loc,"MANAGER")
    base=f"/api/v1/dev/auto/organizations/{org}/warehouse/writeoffs"
    params={"locationId":str(loc)}
    body={"locationId":str(loc),"lotId":str(lot),"quantity":2,
          "reasonCode":"DAMAGED","note":"Синтетическое повреждение"}
    review={"locationId":str(loc),"action":"APPROVE","note":"Осмотрено управляющим"}
    with TestClient(app,base_url="https://127.0.0.1") as client:
        assert client.get(base,params=params).status_code==403
        client.cookies.set("rp_session",d["tokens"][d["intake"]])
        state=client.get(base,params=params)
        assert state.status_code==200 and state.headers["cache-control"]=="no-store"
        assert state.json()["lots"][0]["available"]=="5"
        assert client.get(base,params={**params,"locationId":str(uuid4())}).status_code==403
        assert client.get(base,params={**params,"cursor":"broken"}).status_code==422
        assert post(client,d,d["intake"],base,body,csrf=False).status_code==403
        assert post(client,d,d["manager"],base,body).status_code==403
        assert post(client,d,d["intake"],base,{**body,"quantity":True}).status_code==422
        assert post(client,d,d["intake"],base,{**body,"lotId":str(uuid4())}).status_code==403
        key=uuid4()
        receipt=post(client,d,d["intake"],base,body,key)
        assert receipt.status_code==201,receipt.text
        ident=receipt.json()["documentId"]
        assert post(client,d,d["intake"],base,body,key).status_code==200
        assert post(client,d,d["intake"],base,{**body,"quantity":1},key).status_code==409
        with psycopg.connect(DSN) as db:
            assert balance(db,item)==(8,3) and allocated(db,lot)==8
        path=base+"/"+ident+"/decisions"
        assert post(client,d,d["intake"],path,review).status_code==403
        assert post(client,d,d["manager"],path,review,csrf=False).status_code==403
        client.cookies.set("rp_session",d["tokens"][d["manager"]])
        queue=client.get(base,params={**params,"status":"PENDING"}).json()
        assert not queue["canDraft"] and queue["lots"]==[]
        assert queue["items"][0]["canReview"] and not queue["items"][0]["canCancel"]
        approval_key=uuid4()
        approved=post(client,d,d["manager"],path,review,approval_key)
        assert approved.status_code==201,approved.text
        assert post(client,d,d["manager"],path,review,approval_key).status_code==200
        assert post(client,d,d["manager"],path,{**review,"note":"Другое решение"},approval_key).status_code==409
        assert post(client,d,d["manager"],path,{**review,"action":"REJECT"}).status_code==409
        history=client.get(base,params={**params,"status":"APPROVED"}).json()["items"]
        row=history[0]
        assert row["documentId"]==ident and row["status"]=="APPROVED"
        assert row["createdBy"]==str(d["intake"])
        assert row["decision"]["actorId"]==str(d["manager"])
        with psycopg.connect(DSN) as db:
            assert balance(db,item)==(6,3) and allocated(db,lot)==6
            assert db.execute("""SELECT e.action,a.action,p.quantity
                FROM auto_stock_lot_disposition p JOIN auto_stock_event e ON e.id=p.stock_event_id
                JOIN auto_stock_lot_allocation a ON a.id=p.lot_debit_id WHERE p.id=%s""",
                (row["decision"]["dispositionId"],)).fetchone()==("WRITE_OFF","DEALLOCATE",2)
            for table,field,identity in (
                ("auto_stock_writeoff_document","id",ident),
                ("auto_stock_writeoff_decision","id",row["decision"]["id"]),
            ):
                with pytest.raises(psycopg.errors.RaiseException):
                    with db.transaction():
                        db.execute(f"DELETE FROM {table} WHERE {field}=%s",(identity,))
        for action,actor,status in (("REJECT",d["manager"],"REJECTED"),("CANCEL",d["intake"],"CANCELED")):
            created=post(client,d,d["intake"],base,body)
            assert created.status_code==201,created.text
            doc=created.json()["documentId"]
            decision_path=base+"/"+doc+"/decisions"
            result=post(client,d,actor,decision_path,{**review,"action":action})
            assert result.status_code==201,result.text
            entry=client.get(base,params={**params,"status":status}).json()["items"][0]
            assert entry["documentId"]==doc and entry["decision"]["stockEventId"] is None
        too_large=post(client,d,d["intake"],base,{**body,"quantity":4})
        assert too_large.status_code==201,too_large.text
        assert post(client,d,d["manager"],base+"/"+too_large.json()["documentId"]+"/decisions",review).status_code==409
        # Два решения одного акта конкурируют на строке документа: только одно списание.
        created=post(client,d,d["intake"],base,{**body,"quantity":1})
        assert created.status_code==201
        from uuid import UUID
        document=UUID(created.json()["documentId"])
        barrier=Barrier(2)
        def approve_once(key):
            barrier.wait(timeout=10)
            try:
                with psycopg.connect(DSN) as db:
                    decide(db,d["tokens"][d["manager"]],org,loc,document,key,"APPROVE","Подтверждено")
                return "APPROVED"
            except AutoStockConflict:
                return "CONFLICT"
        with ThreadPoolExecutor(max_workers=2) as pool:
            assert sorted(pool.map(approve_once,(uuid4(),uuid4())))==["APPROVED","CONFLICT"]
        with psycopg.connect(DSN) as db:
            assert balance(db,item)==(5,3) and allocated(db,lot)==5
            assert db.execute("SELECT count(*) FROM auto_stock_writeoff_decision WHERE document_id=%s",(document,)).fetchone()==(1,)
            db.execute("UPDATE auto_organization_role_grant SET revoked_at=now() WHERE id=%s",(manager_grant,))
        client.cookies.set("rp_session",d["tokens"][d["manager"]])
        assert client.get(base,params=params).status_code==403
        assert post(client,d,d["manager"],path,review,approval_key).status_code==403
