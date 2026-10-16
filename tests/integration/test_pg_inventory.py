"""Real PostgreSQL reservation, replay, cancellation and two-connection race."""
from datetime import datetime,timedelta,timezone
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4,UUID
import psycopg
import pytest
from fastapi.testclient import TestClient
from roboport_api.main import app
from roboport_api.inventory import reserve,cancel,StockConflict,StockForbidden
from roboport_api.session_tokens import new_token,token_digest
from test_pg_baseline import DSN


def inventory_fixture():
    customer,device,ticket,lot,actor,match,part,estimate,model,engineer,manager=(uuid4() for _ in range(11))
    token,csrf=new_token(),new_token()
    now=datetime.now(timezone.utc)
    with psycopg.connect(DSN) as db:
        db.execute("INSERT INTO customer(id,label) VALUES (%s,%s)",(customer,customer.hex))
        db.execute("""INSERT INTO robot_device(id,model_variant,region,revision)
          VALUES (%s,'MODEL-X','RU','R2')""",(device,))
        db.execute("""INSERT INTO service_ticket(id,customer_id,device_id,state)
          VALUES (%s,%s,%s,'APPROVED')""",(ticket,customer,device))
        db.execute("INSERT INTO stock_lot(id,part_number,available_qty) VALUES (%s,%s,1)",(lot,'TEST-PN-'+lot.hex))
        db.execute("INSERT INTO inventory_part(id,part_number) VALUES (%s,%s)",
                   (part,'TEST-PN-'+lot.hex))
        db.execute("""INSERT INTO catalog_model
          (id,brand,model,variant,dock,region,revision,status)
          VALUES (%s,%s,'SYNTH-ROBOT','MODEL-X','DOCK-1','RU','R2','SUPPORTED')""",(model,'SYNTH-BRAND-'+model.hex))
        db.execute("""INSERT INTO part_compatibility
          (id,model_id,part_number,evidence)
          VALUES (%s,%s,%s,'synthetic-evidence')""",(match,model,'TEST-PN-'+lot.hex))
        for reviewer,role in ((engineer,'ENGINEER'),(manager,'MANAGER')):
            db.execute("INSERT INTO app_user(id,normalized_login) VALUES(%s,%s)",(reviewer,reviewer.hex))
            db.execute("INSERT INTO user_role(id,user_id,role_code) VALUES(%s,%s,%s)",
                       (uuid4(),reviewer,role))
        for reviewer,role in ((engineer,'ENGINEER'),(manager,'MANAGER')):
            db.execute("""INSERT INTO part_compatibility_approval
              (id,compatibility_id,actor_id,role,approved) VALUES(%s,%s,%s,%s,true)""",
                       (uuid4(),match,reviewer,role))
        db.execute("INSERT INTO app_user(id,normalized_login) VALUES (%s,%s)",(actor,actor.hex))
        db.execute("INSERT INTO user_role(id,user_id,role_code) VALUES (%s,%s,'STOCK')",(uuid4(),actor))
        db.execute("""INSERT INTO acl_entry(id,user_id,ticket_id,action)
          VALUES (%s,%s,%s,'stock.reserve')""",(uuid4(),actor,ticket))
        db.execute("""INSERT INTO user_session(id,user_id,token_hash,csrf_hash,
          created_at,idle_expires_at,absolute_expires_at)
          VALUES (%s,%s,%s,%s,%s,%s,%s)""",
          (uuid4(),actor,token_digest(token),token_digest(csrf),now,
           now+timedelta(minutes=30),now+timedelta(hours=4)))
        db.execute("""INSERT INTO estimate(id,ticket_id,version,state,total_minor,currency)
          VALUES (%s,%s,1,'SENT',100,'RUB')""",(estimate,ticket))
        db.execute("""INSERT INTO estimate_approval(id,estimate_id,actor_id,decision)
          VALUES (%s,%s,%s,'APPROVED')""",(uuid4(),estimate,actor))
    return ticket,lot,actor,match,part,model,token,csrf


def test_unverified_then_reserve_replay_and_release(monkeypatch):
    ticket,lot,actor,match,part,model,token,csrf=inventory_fixture()
    with psycopg.connect(DSN) as db:
        with pytest.raises(StockConflict):
            reserve(db,token,ticket,lot,1,"unverified")
        assert db.execute("SELECT reserved_qty FROM stock_lot WHERE id=%s",(lot,)).fetchone()[0]==0
        db.execute("UPDATE catalog_model SET revision='R3' WHERE id=%s",(model,))
        with pytest.raises(StockConflict):
            reserve(db,token,ticket,lot,1,"wrong-revision")
        assert db.execute("SELECT reserved_qty FROM stock_lot WHERE id=%s",(lot,)).fetchone()[0]==0
        db.execute("UPDATE catalog_model SET revision='R2' WHERE id=%s",(model,))
        db.execute("UPDATE part_compatibility SET status='SUPPORTED' WHERE id=%s",(match,))
    monkeypatch.setenv("ROBOport_SYNTHETIC_ACCESS_API","1")
    monkeypatch.setenv("ROBOport_DATABASE_URL",DSN)
    http=TestClient(app,base_url="https://127.0.0.1")
    path="/api/v1/dev/inventory/reservations"
    headers={"Origin":"https://127.0.0.1","X-CSRF-Token":csrf,
             "Idempotency-Key":"reserve-"+ticket.hex}
    payload={"ticketId":str(ticket),"lotId":str(lot),"quantity":1}
    assert http.post(path,json=payload,headers=headers).status_code==401
    http.cookies.set("rp_session",token)
    assert http.post(path,json=payload,headers={**headers,"X-CSRF-Token":"invalid"}).status_code==403
    ok=http.post(path,json=payload,headers=headers)
    assert ok.status_code==200,ok.text
    assert http.post(path,json=payload,headers=headers).json()==ok.json()
    assert http.post(path,json={**payload,"quantity":2},headers=headers).status_code==409
    rid=UUID(ok.json()["id"])
    with psycopg.connect(DSN) as db:
        assert db.execute("SELECT reserved_qty FROM stock_lot WHERE id=%s",(lot,)).fetchone()[0]==1
        assert db.execute("SELECT count(*) FROM stock_movement WHERE reservation_id=%s",(rid,)).fetchone()[0]==1
        db.execute("UPDATE acl_entry SET revoked_at=now() WHERE user_id=%s",(actor,))
    assert http.post(path,json=payload,headers=headers).status_code==404
    with psycopg.connect(DSN) as db:
        db.execute("""INSERT INTO acl_entry(id,user_id,ticket_id,action)
                      VALUES (%s,%s,%s,'stock.reserve')""",(uuid4(),actor,ticket))
    cancel_path=f"/api/v1/dev/inventory/reservations/{rid}/cancel"
    ch={**headers,"Idempotency-Key":"cancel-"+rid.hex}
    assert http.post(cancel_path,headers=ch).status_code==200
    assert http.post(cancel_path,headers=ch).status_code==200
    assert http.post(cancel_path,headers={**ch,"Idempotency-Key":"cancel-again-"+rid.hex}).status_code==200
    with psycopg.connect(DSN) as db:
        assert db.execute("SELECT reserved_qty FROM stock_lot WHERE id=%s",(lot,)).fetchone()[0]==0
        assert db.execute("SELECT count(*) FROM stock_movement WHERE reservation_id=%s",(rid,)).fetchone()[0]==2
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("UPDATE stock_reservation SET qty=2 WHERE id=%s",(rid,))
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("UPDATE stock_reservation SET status='ACTIVE',cancelled_at=NULL WHERE id=%s",(rid,))
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("DELETE FROM stock_reservation WHERE id=%s",(rid,))


def test_parallel_last_unit_two_connections():
    ticket,lot,actor,match,part,model,token,csrf=inventory_fixture()
    with psycopg.connect(DSN) as db:
        db.execute("UPDATE part_compatibility SET status='SUPPORTED' WHERE id=%s",(match,))
    barrier=Barrier(2)
    def attempt(i):
        try:
            with psycopg.connect(DSN) as db:
                barrier.wait(timeout=20)
                reserve(db,token,ticket,lot,1,f"parallel-{i}")
            return "success"
        except StockConflict:
            return "conflict"
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures=[pool.submit(attempt,i) for i in range(2)]
        results=[f.result(timeout=30) for f in futures]
    assert sorted(results)==["conflict","success"],results
    with psycopg.connect(DSN) as db:
        assert db.execute("SELECT reserved_qty FROM stock_lot WHERE id=%s",(lot,)).fetchone()[0]==1
        assert db.execute("SELECT count(*) FROM stock_movement WHERE lot_id=%s",(lot,)).fetchone()[0]==1


def test_public_contract_part_version_and_release_reason(monkeypatch):
    ticket,lot,actor,match,part,model,token,csrf=inventory_fixture()
    with psycopg.connect(DSN) as db:
        db.execute("UPDATE part_compatibility SET status='SUPPORTED' WHERE id=%s",(match,))
    monkeypatch.setenv("ROBOport_SYNTHETIC_ACCESS_API","1")
    monkeypatch.setenv("ROBOport_DATABASE_URL",DSN)
    http=TestClient(app,base_url="https://127.0.0.1")
    http.cookies.set("rp_session",token)
    headers={"Origin":"https://127.0.0.1","X-CSRF-Token":csrf,
             "Idempotency-Key":"public-"+ticket.hex}
    path="/api/v1/staff/stock/reservations"
    payload={"ticketId":str(ticket),"lotId":str(lot),"partId":str(part),
             "quantity":1,"expectedTicketVersion":1}
    assert http.post(path,json={**payload,"expectedTicketVersion":2},headers=headers).status_code==409
    assert http.post(path,json={**payload,"partId":str(uuid4())},headers=headers).status_code==409
    ok=http.post(path,json=payload,headers=headers)
    assert ok.status_code==200,ok.text
    assert set(ok.json())=={"id","status"}
    assert http.post(path,json=payload,headers=headers).json()==ok.json()
    rid=ok.json()["id"]
    cancel_url=f"/api/v1/staff/stock/reservations/{rid}/release"
    ch={**headers,"Idempotency-Key":"release-"+rid}
    missing=http.post(cancel_url,json={},headers=ch)
    assert missing.status_code==422,missing.text
    released=http.post(cancel_url,json={"reason":"Client cancelled"},headers=ch)
    assert released.status_code==200,released.text
    assert released.json()=={"id":rid,"status":"CANCELLED"}
    assert http.post(cancel_url,json={"reason":"Client cancelled"},headers=ch).json()==released.json()
    assert http.post(cancel_url,json={"reason":"Other reason"},headers=ch).status_code==409
    with psycopg.connect(DSN) as db:
        assert db.execute("SELECT reserved_qty FROM stock_lot WHERE id=%s",(lot,)).fetchone()[0]==0
        assert db.execute("SELECT count(*) FROM stock_movement WHERE lot_id=%s",(lot,)).fetchone()[0]==2
