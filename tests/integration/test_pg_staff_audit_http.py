"""Auditor role and ticket ACL are both mandatory for journal read."""
from datetime import datetime,timedelta,timezone
from uuid import uuid4
import psycopg
from fastapi.testclient import TestClient
from roboport_api.main import app
from roboport_api.session_tokens import new_token,token_digest
from test_pg_baseline import DSN

def test_auditor_only_sees_assigned_audit_events(monkeypatch):
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTH_API","1")
    monkeypatch.setenv("ROBOport_DATABASE_URL",DSN)
    auditor,customer,other_customer,ticket,other_ticket=(uuid4() for _ in range(5))
    role,acl,session=uuid4(),uuid4(),uuid4()
    token=new_token()
    now=datetime.now(timezone.utc)
    with psycopg.connect(DSN) as db:
        db.execute("INSERT INTO app_user(id,normalized_login) VALUES(%s,%s)",
                   (auditor,"staff-audit-"+auditor.hex))
        for c in (customer,other_customer):
            db.execute("INSERT INTO customer(id,label) VALUES(%s,%s)",(c,c.hex))
        for t,c in ((ticket,customer),(other_ticket,other_customer)):
            db.execute("INSERT INTO service_ticket(id,customer_id) VALUES(%s,%s)",(t,c))
            db.execute("INSERT INTO audit_event(id,ticket_id,action) VALUES(%s,%s,%s)",
                       (uuid4(),t,"ticket.transition"))
        db.execute("INSERT INTO user_role(id,user_id,role_code) VALUES(%s,%s,'AUDITOR')",
                   (role,auditor))
        db.execute("INSERT INTO acl_entry(id,user_id,ticket_id,action) VALUES(%s,%s,%s,'ticket.read')",
                   (acl,auditor,ticket))
        db.execute("""INSERT INTO user_session
           (id,user_id,token_hash,csrf_hash,created_at,idle_expires_at,absolute_expires_at)
           VALUES(%s,%s,%s,%s,%s,%s,%s)""",
           (session,auditor,token_digest(token),token_digest(new_token()),now,
            now+timedelta(minutes=30),now+timedelta(hours=4)))
    c=TestClient(app,base_url="https://127.0.0.1")
    url="/api/v1/dev/staff/audit"
    assert c.get(url).status_code==401
    c.cookies.set("rp_session",token)
    response=c.get(url)
    assert response.status_code==200,response.text
    assert response.headers["cache-control"]=="no-store"
    items=response.json()["items"]
    assert len(items)==1 and items[0]["ticketId"]==str(ticket)
    assert str(other_ticket) not in response.text
    with psycopg.connect(DSN) as db:
        db.execute("UPDATE acl_entry SET revoked_at=now() WHERE id=%s",(acl,))
    assert c.get(url).json()["items"]==[]
    with psycopg.connect(DSN) as db:
        db.execute("UPDATE user_role SET revoked_at=now() WHERE id=%s",(role,))
    assert c.get(url).status_code==403
    with psycopg.connect(DSN) as db:
        db.execute("UPDATE user_session SET revoked_at=now() WHERE id=%s",(session,))
    assert c.get(url).status_code==401
