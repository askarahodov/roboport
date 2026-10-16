"""Real PostgreSQL HTTP isolation of synthetic customer collections, no bearer fixtures."""
from datetime import datetime,timedelta,timezone
from uuid import uuid4
import psycopg
from fastapi.testclient import TestClient
from roboport_api.main import app
from roboport_api.session_tokens import new_token,token_digest
from test_pg_baseline import DSN


def test_live_customer_session_a_b_revocation_and_no_document_object_key(monkeypatch):
    monkeypatch.setenv("ROBOport_SYNTHETIC_ACCESS_API","1")
    monkeypatch.setenv("ROBOport_DATABASE_URL",DSN)
    a,b,customer_a,customer_b,ticket_a,ticket_b,device_a,device_b=(uuid4() for _ in range(8))
    estimate_a,estimate_b,attachment_a,attachment_b=(uuid4() for _ in range(4))
    token_a,token_b=new_token(),new_token()
    now=datetime.now(timezone.utc)
    with psycopg.connect(DSN) as db:
        for subject,customer,token in ((a,customer_a,token_a),(b,customer_b,token_b)):
            db.execute("INSERT INTO app_user(id,normalized_login) VALUES(%s,%s)",
                       (subject,"acct-"+subject.hex))
            db.execute("INSERT INTO user_role(id,user_id,role_code) VALUES (%s,%s,'CLIENT')",
                       (uuid4(),subject))
            db.execute("INSERT INTO customer(id,label) VALUES(%s,%s)",(customer,customer.hex))
            db.execute("INSERT INTO account_customer_link(user_id,customer_id) VALUES(%s,%s)",
                       (subject,customer))
            db.execute("""INSERT INTO user_session(id,user_id,token_hash,csrf_hash,
                        created_at,idle_expires_at,absolute_expires_at)
                        VALUES(%s,%s,%s,%s,%s,%s,%s)""",
                        (uuid4(),subject,token_digest(token),token_digest(new_token()),now,
                         now+timedelta(minutes=30),now+timedelta(hours=4)))
        for device in (device_a,device_b):
            db.execute("INSERT INTO robot_device(id,serial_number) VALUES(%s,%s)",
                       (device,device.hex))
        for ticket,customer,device in ((ticket_a,customer_a,device_a),(ticket_b,customer_b,device_b)):
            db.execute("INSERT INTO service_ticket(id,customer_id,device_id) VALUES(%s,%s,%s)",
                       (ticket,customer,device))
        for estimate,ticket in ((estimate_a,ticket_a),(estimate_b,ticket_b)):
            db.execute("""INSERT INTO estimate(id,ticket_id,version,state,total_minor,currency)
                         VALUES(%s,%s,1,'DRAFT',750,'RUB')""",(estimate,ticket))
        for attachment,ticket,actor in ((attachment_a,ticket_a,a),(attachment_b,ticket_b,b)):
            db.execute("""INSERT INTO attachment(id,ticket_id,object_key,mime,byte_size,sha256,
                         upload_state,quarantine_state,created_by)
                         VALUES (%s,%s,%s,'application/pdf',100,%s,'READY','CLEAR',%s)""",
                       (attachment,ticket,attachment.hex,'a'*64,actor))
    http=TestClient(app,base_url="https://127.0.0.1")
    assert http.get("/api/v1/dev/account/collections/robots").status_code==401
    for token,own,other in ((token_a,(device_a,ticket_a,estimate_a,attachment_a),(device_b,ticket_b,estimate_b,attachment_b)),
                            (token_b,(device_b,ticket_b,estimate_b,attachment_b),(device_a,ticket_a,estimate_a,attachment_a))):
        http.cookies.clear()
        http.cookies.set("rp_session",token)
        for kind,expected,foreign in zip(("robots","tickets","estimates","documents"),own,other):
            response=http.get(f"/api/v1/dev/account/collections/{kind}")
            assert response.status_code==200,response.text
            items=response.json()["items"]
            assert [row["id"] for row in items]==[str(expected)]
            assert str(foreign) not in response.text
            assert "object_key" not in response.text and "sha256" not in response.text
    with psycopg.connect(DSN) as db:
        db.execute("UPDATE account_customer_link SET revoked_at=now() WHERE user_id=%s",(a,))
    http.cookies.clear()
    http.cookies.set("rp_session",token_a)
    for kind in ("robots","tickets","estimates","documents"):
        result=http.get(f"/api/v1/dev/account/collections/{kind}")
        assert result.status_code==200 and result.json()=={"items":[]}
    with psycopg.connect(DSN) as db:
        db.execute("UPDATE user_session SET revoked_at=now() WHERE user_id=%s",(a,))
    assert http.get("/api/v1/dev/account/collections/estimates").status_code==401
    http.cookies.clear()
    http.cookies.set("rp_session",token_b)
    assert http.get("/api/v1/dev/account/collections/documents").json()["items"][0]["id"]==str(attachment_b)
    with psycopg.connect(DSN) as db:
        db.execute("UPDATE user_role SET revoked_at=now() WHERE user_id=%s AND role_code='CLIENT'",(b,))
    assert http.get("/api/v1/dev/account/collections/tickets").status_code==401
    assert http.get("/api/v1/dev/account/collections/unknown").status_code==404
    monkeypatch.delenv("ROBOport_SYNTHETIC_ACCESS_API")
    assert http.get("/api/v1/dev/account/collections/robots").status_code==404


def test_two_http_logins_use_distinct_server_sessions_and_object_scopes(monkeypatch):
    from roboport_api.passwords import hash_password
    monkeypatch.setenv("ROBOport_SYNTHETIC_ACCESS_API","1")
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTH_API","1")
    monkeypatch.setenv("ROBOport_DATABASE_URL",DSN)
    user_a,user_b,customer_a,customer_b,ticket_a,ticket_b=(uuid4() for _ in range(6))
    password="Synthetic-Only-Passphrase-12345!"
    with psycopg.connect(DSN) as db:
        for user,customer,ticket in ((user_a,customer_a,ticket_a),(user_b,customer_b,ticket_b)):
            db.execute("INSERT INTO app_user(id,normalized_login) VALUES(%s,%s)",(user,user.hex))
            db.execute("""INSERT INTO local_credential(user_id,password_hash,must_change_password)
              VALUES (%s,%s,false)""",(user,hash_password(password)))
            db.execute("INSERT INTO user_role(id,user_id,role_code) VALUES (%s,%s,'CLIENT')",
                       (uuid4(),user))
            db.execute("INSERT INTO customer(id,label) VALUES (%s,%s)",(customer,customer.hex))
            db.execute("INSERT INTO account_customer_link(user_id,customer_id) VALUES(%s,%s)",(user,customer))
            db.execute("INSERT INTO service_ticket(id,customer_id) VALUES (%s,%s)",(ticket,customer))
    clients=[]
    origin={"Origin":"https://127.0.0.1"}
    for user,own,foreign in ((user_a,ticket_a,ticket_b),(user_b,ticket_b,ticket_a)):
        client=TestClient(app,base_url="https://127.0.0.1")
        login=client.post("/api/v1/auth/login",
                          json={"login":user.hex,"password":password},headers=origin)
        assert login.status_code==200,login.text
        cookie=login.headers.get("set-cookie","")
        assert "HttpOnly" in cookie and "Secure" in cookie and "SameSite=lax" in cookie.lower().replace("samesite=lax","SameSite=lax")
        assert client.get("/api/v1/auth/me").json()["subjectId"]==str(user)
        page=client.get("/api/v1/dev/tickets/keyset")
        assert [x["id"] for x in page.json()["items"]]==[str(own)]
        assert str(foreign) not in page.text
        clients.append((client,login.json()["csrfToken"]))
    first,csrf=clients[0]
    bad=first.post("/api/v1/auth/logout",headers=origin)
    assert bad.status_code==403
    good=first.post("/api/v1/auth/logout",headers={**origin,"X-CSRF-Token":csrf})
    assert good.status_code==200
    assert first.get("/api/v1/auth/me").status_code==401
    assert first.get("/api/v1/dev/tickets/keyset").status_code==401
    assert clients[1][0].get("/api/v1/dev/tickets/keyset").status_code==200
