"""Full real PostgreSQL + real Docker MinIO + HTTP ACL test."""
from datetime import datetime,timedelta,timezone
from uuid import uuid4
import os
import pytest
import psycopg
from fastapi.testclient import TestClient
from roboport_api.main import app
from roboport_api.storage import MinioFileStorage
from roboport_api.session_tokens import new_token,token_digest
from test_pg_baseline import DSN


@pytest.mark.skipif(os.getenv('ROBOport_MINIO_ENDPOINT') != 'http://127.0.0.1:9000',
                    reason='needs real isolated MinIO and PostgreSQL')
def test_real_private_http_document_bytes_then_revoke_and_quarantine(monkeypatch):
    monkeypatch.setenv("ROBOport_SYNTHETIC_ACCESS_API","1")
    monkeypatch.setenv("ROBOport_DATABASE_URL",DSN)
    storage=MinioFileStorage.from_environment()
    payload=b"synthetic-contract-attachment"
    obj=storage.put(payload,"text/plain")
    customer,user,ticket,attachment,session,grant=(uuid4() for _ in range(6))
    token=new_token()
    now=datetime.now(timezone.utc)
    with psycopg.connect(DSN) as db:
        db.execute("INSERT INTO app_user(id,normalized_login) VALUES(%s,%s)",
                   (user,"full-minio-"+user.hex))
        db.execute("INSERT INTO customer(id,label) VALUES(%s,%s)",(customer,customer.hex))
        db.execute("INSERT INTO service_ticket(id,customer_id) VALUES(%s,%s)",(ticket,customer))
        db.execute("INSERT INTO account_customer_link(user_id,customer_id) VALUES(%s,%s)",
                   (user,customer))
        db.execute("INSERT INTO user_role(id,user_id,role_code) VALUES(%s,%s,'CLIENT')",(grant,user))
        db.execute("""INSERT INTO user_session(id,user_id,token_hash,csrf_hash,created_at,idle_expires_at,absolute_expires_at)
                      VALUES(%s,%s,%s,%s,%s,%s,%s)""",
                   (session,user,token_digest(token),token_digest(new_token()),now,
                    now+timedelta(minutes=30),now+timedelta(hours=4)))
        db.execute("""INSERT INTO attachment(id,ticket_id,object_key,mime,byte_size,sha256,created_by,upload_state)
                      VALUES(%s,%s,%s,%s,%s,%s,%s,'READY')""",
                   (attachment,ticket,obj.key,obj.mime,obj.size,obj.sha256,user))
    client=TestClient(app,base_url="https://127.0.0.1")
    url=f"/api/v1/dev/attachments/{attachment}/content"
    assert client.get(url).status_code==401
    client.cookies.set("rp_session",token)
    granted=client.get(url)
    assert granted.status_code==200,granted.text
    assert granted.content==payload
    assert granted.headers["cache-control"]=="no-store"
    assert granted.headers["content-disposition"]=="attachment"
    # A different random document identifier is never fetchable.
    assert client.get(f"/api/v1/dev/attachments/{uuid4()}/content").status_code==404
    with psycopg.connect(DSN) as db:
        db.execute("UPDATE user_role SET revoked_at=now() WHERE id=%s",(grant,))
    assert client.get(url,headers={"Idempotency-Key":"synthetic-replay"}).status_code==404
    with psycopg.connect(DSN) as db:
        db.execute("UPDATE attachment SET quarantine_state='QUARANTINED' WHERE id=%s",
                   (attachment,))
        result=db.execute("""SELECT outcome,count(*) FROM authorization_audit
                             WHERE action='attachment.read' GROUP BY outcome""").fetchall()
    assert dict(result).get("ALLOW",0)>=1
    assert client.get(url).status_code==404
    storage.quarantine(obj.key)


@pytest.mark.skipif(os.getenv("ROBOport_MINIO_ENDPOINT")!="http://127.0.0.1:9000",
                    reason="requires real PostgreSQL and MinIO")
def test_csrf_protected_upload_download_and_revoke(monkeypatch):
    monkeypatch.setenv("ROBOport_SYNTHETIC_ACCESS_API","1")
    monkeypatch.setenv("ROBOport_DATABASE_URL",DSN)
    user,customer,ticket,grant=(uuid4() for _ in range(4))
    token,csrf=new_token(),new_token()
    now=datetime.now(timezone.utc)
    with psycopg.connect(DSN) as db:
        db.execute("INSERT INTO app_user(id,normalized_login) VALUES(%s,%s)",
                   (user,"upload-"+user.hex))
        db.execute("INSERT INTO customer(id,label) VALUES(%s,%s)",(customer,customer.hex))
        db.execute("INSERT INTO service_ticket(id,customer_id) VALUES(%s,%s)",(ticket,customer))
        db.execute("INSERT INTO account_customer_link(user_id,customer_id) VALUES(%s,%s)",
                   (user,customer))
        db.execute("INSERT INTO user_role(id,user_id,role_code) VALUES(%s,%s,'CLIENT')",(grant,user))
        db.execute("""INSERT INTO user_session(id,user_id,token_hash,csrf_hash,created_at,idle_expires_at,absolute_expires_at)
                      VALUES(%s,%s,%s,%s,%s,%s,%s)""",
                   (uuid4(),user,token_digest(token),token_digest(csrf),now,
                    now+timedelta(minutes=30),now+timedelta(hours=4)))
    client=TestClient(app,base_url="https://127.0.0.1")
    client.cookies.set("rp_session",token)
    url=f"/api/v1/dev/tickets/{ticket}/attachments"
    data=b"synthetic-upload-through-http"
    headers={"Content-Type":"text/plain","Origin":"https://127.0.0.1",
             "X-CSRF-Token":csrf}
    assert client.post(url,content=data,headers={k:v for k,v in headers.items()
                  if k!="X-CSRF-Token"}).status_code==403
    success=client.post(url,content=data,headers=headers)
    assert success.status_code==201,success.text
    attachment_id=success.json()["id"]
    read=f"/api/v1/dev/attachments/{attachment_id}/content"
    result=client.get(read)
    assert result.status_code==200,result.text
    assert result.content==data
    with psycopg.connect(DSN) as db:
        assert db.execute("SELECT upload_state FROM attachment WHERE id=%s",
                          (attachment_id,)).fetchone()[0]=="READY"
        db.execute("UPDATE user_role SET revoked_at=now() WHERE id=%s",(grant,))
    assert client.get(read).status_code==404
    assert client.post(url,content=data,headers=headers).status_code==404
