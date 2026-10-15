"""Actual PostgreSQL HTTP access checks; MinIO never contacted when denied."""
from datetime import datetime,timedelta,timezone
from uuid import uuid4
import hashlib
import psycopg
from fastapi.testclient import TestClient
from roboport_api.main import app
from roboport_api.session_tokens import new_token,token_digest
from test_pg_baseline import DSN


def test_attachment_acl_quarantine_and_role_revoke_http(monkeypatch):
    monkeypatch.setenv("ROBOport_SYNTHETIC_ACCESS_API","1")
    monkeypatch.setenv("ROBOport_DATABASE_URL",DSN)
    actors=[uuid4(),uuid4()]
    customers=[uuid4(),uuid4()]
    tickets=[uuid4(),uuid4()]
    attachments=[uuid4(),uuid4()]
    grants=[uuid4(),uuid4()]
    tokens=[new_token(),new_token()]
    now=datetime.now(timezone.utc)
    payload=b"synthetic payload"
    digest=hashlib.sha256(payload).hexdigest()
    with psycopg.connect(DSN) as db:
        for i in range(2):
            db.execute("INSERT INTO app_user(id,normalized_login) VALUES(%s,%s)",
                       (actors[i],"doc-"+actors[i].hex))
            db.execute("INSERT INTO customer(id,label) VALUES(%s,%s)",
                       (customers[i],customers[i].hex))
            db.execute("INSERT INTO service_ticket(id,customer_id) VALUES(%s,%s)",
                       (tickets[i],customers[i]))
            db.execute("INSERT INTO account_customer_link(user_id,customer_id) VALUES(%s,%s)",
                       (actors[i],customers[i]))
            db.execute("INSERT INTO user_role(id,user_id,role_code) VALUES(%s,%s,'CLIENT')",
                       (grants[i],actors[i]))
            db.execute("""INSERT INTO user_session(id,user_id,token_hash,csrf_hash,created_at,idle_expires_at,absolute_expires_at)
                          VALUES(%s,%s,%s,%s,%s,%s,%s)""",
                       (uuid4(),actors[i],token_digest(tokens[i]),token_digest(new_token()),
                        now,now+timedelta(minutes=30),now+timedelta(hours=4)))
            db.execute("""INSERT INTO attachment(id,ticket_id,object_key,mime,byte_size,sha256,created_by,upload_state)
                          VALUES(%s,%s,%s,'text/plain',%s,%s,%s,'READY')""",
                       (attachments[i],tickets[i],uuid4().hex,len(payload),digest,actors[i]))
    client=TestClient(app,base_url="https://127.0.0.1")
    route=lambda a:f"/api/v1/dev/attachments/{a}/content"
    assert client.get(route(attachments[0])).status_code==401
    client.cookies.set("rp_session",tokens[0])
    # A's valid authorization reaches storage; no local MinIO means safe 503.
    assert client.get(route(attachments[0])).status_code==503
    assert client.get(route(attachments[1])).status_code==404
    with psycopg.connect(DSN) as db:
        db.execute("UPDATE attachment SET quarantine_state='QUARANTINED' WHERE id=%s",
                   (attachments[0],))
    assert client.get(route(attachments[0])).status_code==404
    with psycopg.connect(DSN) as db:
        db.execute("UPDATE user_role SET revoked_at=now() WHERE id=%s",(grants[0],))
    assert client.get(route(attachments[0])).status_code==404
    client.cookies.clear()
    client.cookies.set("rp_session",tokens[1])
    assert client.get(route(attachments[0]),headers={"X-User-ID":str(actors[0])}).status_code==404
    with psycopg.connect(DSN) as db:
        db.execute("UPDATE account_customer_link SET revoked_at=now() WHERE user_id=%s",
                   (actors[1],))
    assert client.get(route(attachments[1])).status_code==404
    with psycopg.connect(DSN) as db:
        audits=db.execute("""SELECT outcome,count(*) FROM authorization_audit
                             WHERE action='attachment.read' GROUP BY outcome""").fetchall()
    assert dict(audits).get("DENY",0)>=4
