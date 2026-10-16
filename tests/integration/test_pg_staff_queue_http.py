"""Real PostgreSQL + HTTP negative authorization tests for staff ticket queue."""
from datetime import datetime, timedelta, timezone
from uuid import uuid4
import psycopg
from fastapi.testclient import TestClient
from roboport_api.main import app
from roboport_api.session_tokens import new_token, token_digest
from test_pg_baseline import DSN


def test_staff_queue_is_assigned_only_and_revocation_is_immediate(monkeypatch):
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTH_API", "1")
    monkeypatch.setenv("ROBOport_DATABASE_URL", DSN)
    staff, outsider, customer, other_customer = (uuid4() for _ in range(4))
    assigned, unrelated = uuid4(), uuid4()
    session, role, grant = uuid4(), uuid4(), uuid4()
    token = new_token()
    now = datetime.now(timezone.utc)
    with psycopg.connect(DSN) as db:
        db.execute("INSERT INTO app_user(id,normalized_login) VALUES(%s,%s)",
                   (staff,"staff-queue-"+staff.hex))
        db.execute("INSERT INTO app_user(id,normalized_login) VALUES(%s,%s)",
                   (outsider,"staff-other-"+outsider.hex))
        db.execute("INSERT INTO customer(id,label) VALUES(%s,%s)",
                   (customer,customer.hex))
        db.execute("INSERT INTO customer(id,label) VALUES(%s,%s)",
                   (other_customer,other_customer.hex))
        db.execute("INSERT INTO service_ticket(id,customer_id) VALUES(%s,%s)",
                   (assigned,customer))
        db.execute("INSERT INTO service_ticket(id,customer_id) VALUES(%s,%s)",
                   (unrelated,other_customer))
        db.execute("""INSERT INTO user_session
                   (id,user_id,token_hash,csrf_hash,created_at,idle_expires_at,absolute_expires_at)
                   VALUES(%s,%s,%s,%s,%s,%s,%s)""",
                   (session,staff,token_digest(token),token_digest(new_token()),now,
                    now+timedelta(minutes=30),now+timedelta(hours=4)))
        db.execute("INSERT INTO user_role(id,user_id,role_code) VALUES(%s,%s,'INTAKE')",
                   (role,staff))
        db.execute("INSERT INTO acl_entry(id,user_id,ticket_id,action) VALUES(%s,%s,%s,'ticket.read')",
                   (grant,staff,assigned))
    client=TestClient(app,base_url="https://127.0.0.1")
    url="/api/v1/dev/staff/queue"
    assert client.get(url,headers={"X-User-ID":str(staff)}).status_code==401
    client.cookies.set("rp_session",token)
    invalid=client.get(url+"?limit=51")
    assert invalid.status_code==422
    ok=client.get(url)
    assert ok.status_code==200,ok.text
    assert ok.headers["cache-control"]=="no-store"
    assert ok.json()["items"]==[{"id":str(assigned),"state":"DRAFT","version":1}]
    assert str(unrelated) not in ok.text
    with psycopg.connect(DSN) as db:
        db.execute("UPDATE acl_entry SET revoked_at=now() WHERE id=%s",(grant,))
    empty=client.get(url)
    assert empty.status_code==200 and empty.json()["items"]==[]
    with psycopg.connect(DSN) as db:
        db.execute("UPDATE user_role SET revoked_at=now() WHERE id=%s",(role,))
    assert client.get(url).status_code==403
    with psycopg.connect(DSN) as db:
        db.execute("UPDATE user_session SET revoked_at=now() WHERE id=%s",(session,))
    assert client.get(url).status_code==401


def test_staff_queue_rejects_customer_role_without_grant(monkeypatch):
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTH_API","1")
    monkeypatch.setenv("ROBOport_DATABASE_URL",DSN)
    user, session, role = uuid4(),uuid4(),uuid4()
    token=new_token()
    now=datetime.now(timezone.utc)
    with psycopg.connect(DSN) as db:
        db.execute("INSERT INTO app_user(id,normalized_login) VALUES(%s,%s)",
                   (user,"queue-client-"+user.hex))
        db.execute("""INSERT INTO user_session
                   (id,user_id,token_hash,csrf_hash,created_at,idle_expires_at,absolute_expires_at)
                   VALUES(%s,%s,%s,%s,%s,%s,%s)""",
                   (session,user,token_digest(token),token_digest(new_token()),now,
                    now+timedelta(minutes=30),now+timedelta(hours=4)))
        db.execute("INSERT INTO user_role(id,user_id,role_code) VALUES(%s,%s,'CLIENT')",
                   (role,user))
    client=TestClient(app,base_url="https://127.0.0.1")
    client.cookies.set("rp_session",token)
    assert client.get("/api/v1/dev/staff/queue").status_code==403
