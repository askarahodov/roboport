"""PostgreSQL integration: only own tickets, bounded pages and live revocation."""
from datetime import datetime, timedelta, timezone
from uuid import uuid4
import psycopg
from fastapi.testclient import TestClient
from roboport_api.main import app
from roboport_api.session_tokens import new_token, token_digest
from test_pg_baseline import DSN


def test_owned_ticket_list_pagination_and_revocation(monkeypatch):
    monkeypatch.setenv("ROBOport_SYNTHETIC_ACCESS_API", "1")
    monkeypatch.setenv("ROBOport_DATABASE_URL", DSN)
    uid, mine, theirs, sid = (uuid4() for _ in range(4))
    token = new_token()
    own_ids = [uuid4() for _ in range(3)]
    other_id = uuid4()
    now = datetime.now(timezone.utc)
    with psycopg.connect(DSN) as conn:
        conn.execute("INSERT INTO app_user(id,normalized_login) VALUES (%s,%s)", (uid, "list-"+uid.hex))
        for customer in (mine,theirs):
            conn.execute("INSERT INTO customer(id,label) VALUES (%s,%s)", (customer,str(customer)))
        conn.execute("INSERT INTO account_customer_link(user_id,customer_id) VALUES (%s,%s)", (uid,mine))
        for ticket in own_ids:
            conn.execute("INSERT INTO service_ticket(id,customer_id) VALUES (%s,%s)", (ticket,mine))
        conn.execute("INSERT INTO service_ticket(id,customer_id) VALUES (%s,%s)", (other_id,theirs))
        conn.execute("""INSERT INTO user_session(id,user_id,token_hash,csrf_hash,created_at,idle_expires_at,absolute_expires_at)
           VALUES (%s,%s,%s,%s,%s,%s,%s)""",(sid,uid,token_digest(token),token_digest(new_token()),now,now+timedelta(minutes=30),now+timedelta(hours=4)))
    client=TestClient(app, base_url="https://127.0.0.1")
    path="/api/v1/dev/tickets"
    assert client.get(path,headers={"X-User-ID":str(uid)}).status_code==401
    client.cookies.set("rp_session",token)
    first=client.get(path+"?limit=2")
    second=client.get(path+"?limit=2&offset=2")
    assert first.status_code==second.status_code==200
    items=first.json()["items"]+second.json()["items"]
    assert {entry["id"] for entry in items} == {str(t) for t in own_ids}
    assert all(set(entry)=={"id","state","version"} for entry in items)
    assert client.get(path+"?state=COMPLETED").json()["items"]==[]
    assert client.get(path+"?limit=51").status_code==422
    with psycopg.connect(DSN) as conn:
        conn.execute("UPDATE account_customer_link SET revoked_at=now() WHERE user_id=%s",(uid,))
    assert client.get(path).json()["items"]==[]
    monkeypatch.delenv("ROBOport_SYNTHETIC_ACCESS_API")
    assert client.get(path).status_code==404
