"""End-to-end synthetic financial posting authorization and idempotency."""
from uuid import uuid4
import psycopg
from fastapi.testclient import TestClient
from roboport_api.main import app
from test_pg_auto_order_customer_read import prepare
from test_pg_baseline import DSN


def test_financial_posting_http_csrf_role_scope_idempotency_and_balance(monkeypatch):
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTO_API","1")
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTH_API","1")
    monkeypatch.setenv("ROBOport_DATABASE_URL",DSN)
    d,org,loc,order,foreign,foreign_loc=prepare()
    with psycopg.connect(DSN) as db:
        grant=uuid4()
        db.execute("""INSERT INTO auto_organization_role_grant
          (id,organization_id,location_id,user_id,role_code)
          VALUES(%s,%s,%s,%s,'MANAGER')""",(grant,org,loc,d["manager"]))
    url=f"/api/v1/dev/auto/organizations/{org}/locations/{loc}/orders/{order}/financial-events"
    client=TestClient(app,base_url="https://127.0.0.1")
    payload={"action":"CHARGE","amountMinor":4500,"reason":"synthetic labor"}
    key=str(uuid4())
    headers={"Origin":"https://127.0.0.1","X-CSRF-Token":d["csrf"][d["manager"]],
             "Idempotency-Key":key}
    assert client.post(url,json=payload,headers=headers).status_code==403
    client.cookies.set("rp_session",d["tokens"][d["manager"]])
    assert client.post(url,json=payload).status_code==422
    assert client.post(url,json=payload,headers={"Idempotency-Key":key}).status_code==403
    ok=client.post(url,json=payload,headers=headers)
    assert ok.status_code==201,ok.text
    assert ok.json()["syntheticOnly"] is True
    replay=client.post(url,json=payload,headers=headers)
    assert replay.status_code==201 and replay.json()==ok.json()
    assert client.post(url,json={**payload,"amountMinor":4600},headers=headers).status_code==409
    wrong=url.replace(str(org),str(foreign)).replace(str(loc),str(foreign_loc))
    assert client.post(wrong,json=payload,headers={**headers,"Idempotency-Key":str(uuid4())}).status_code==403
    assert client.post(url,json={**payload,"amountMinor":True},headers={**headers,"Idempotency-Key":str(uuid4())}).status_code==422
    credit={**payload,"action":"CREDIT","amountMinor":1500,"reason":"synthetic correction"}
    assert client.post(url,json=credit,headers={**headers,"Idempotency-Key":str(uuid4())}).status_code==201
    assert client.post(url,json={**credit,"amountMinor":4000},headers={**headers,"Idempotency-Key":str(uuid4())}).status_code==409
    with psycopg.connect(DSN) as db:
        assert db.execute("SELECT balance_minor FROM auto_work_order_financial_balance WHERE work_order_id=%s",(order,)).fetchone()==(3000,)
        assert db.execute("SELECT count(*) FROM auto_work_order_financial_event WHERE work_order_id=%s",(order,)).fetchone()==(2,)
        db.execute("UPDATE auto_organization_role_grant SET revoked_at=now() WHERE id=%s",(grant,))
    assert client.post(url,json=payload,headers={**headers,"Idempotency-Key":str(uuid4())}).status_code==403
    monkeypatch.delenv("ROBOport_SYNTHETIC_AUTO_API")
    assert client.post(url,json=payload,headers=headers).status_code==404
