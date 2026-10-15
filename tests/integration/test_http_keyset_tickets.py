"""Keyset list must isolate clients and resist offset drift."""
from datetime import datetime,timedelta,timezone
from uuid import uuid4
import psycopg
from fastapi.testclient import TestClient
from roboport_api.main import app
from roboport_api.session_tokens import new_token,token_digest
from test_pg_baseline import DSN


def test_keyset_pagination_and_revocation(monkeypatch):
    monkeypatch.setenv("ROBOport_SYNTHETIC_ACCESS_API","1")
    monkeypatch.setenv("ROBOport_DATABASE_URL",DSN)
    user,mine,other,sid=(uuid4() for _ in range(4))
    tickets=[uuid4() for _ in range(3)]
    foreign=uuid4()
    token=new_token()
    now=datetime.now(timezone.utc)
    with psycopg.connect(DSN) as db:
        db.execute("INSERT INTO app_user(id,normalized_login) VALUES (%s,%s)",(user,"cursor-"+user.hex))
        for cid in (mine,other):
            db.execute("INSERT INTO customer(id,label) VALUES (%s,%s)",(cid,str(cid)))
        db.execute("INSERT INTO account_customer_link(user_id,customer_id) VALUES (%s,%s)",(user,mine))
        for tid in tickets:
            db.execute("INSERT INTO service_ticket(id,customer_id) VALUES (%s,%s)",(tid,mine))
        db.execute("INSERT INTO service_ticket(id,customer_id) VALUES (%s,%s)",(foreign,other))
        db.execute("""INSERT INTO user_session(id,user_id,token_hash,csrf_hash,created_at,idle_expires_at,absolute_expires_at)
                      VALUES (%s,%s,%s,%s,%s,%s,%s)""",(sid,user,token_digest(token),
                      token_digest(new_token()),now,now+timedelta(minutes=30),now+timedelta(hours=4)))
    client=TestClient(app)
    path="/api/v1/dev/tickets/keyset"
    assert client.get(path).status_code==401
    client.cookies.set("rp_session",token)
    first=client.get(path,params={"limit":2})
    assert first.status_code==200,first.text
    assert first.json()["nextCursor"]
    second=client.get(path,params={"limit":2,"before":first.json()["nextCursor"]})
    assert second.status_code==200,second.text
    collected=first.json()["items"]+second.json()["items"]
    assert {item["id"] for item in collected}=={str(t) for t in tickets}
    assert all(set(item)=={"id","state","version"} for item in collected)
    assert client.get(path,params={"before":"bad"}).status_code==422
    assert client.get(path,params={"state":"invalid"}).status_code==422
    assert client.get(path,params={"limit":51}).status_code==422
    with psycopg.connect(DSN) as db:
        db.execute("UPDATE account_customer_link SET revoked_at=now() WHERE user_id=%s",(user,))
    assert client.get(path).json()["items"]==[]
    monkeypatch.delenv("ROBOport_SYNTHETIC_ACCESS_API")
    assert client.get(path).status_code==404
