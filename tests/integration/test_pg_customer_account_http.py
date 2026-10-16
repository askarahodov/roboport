"""Customer account HTTP access: independent real sessions A/B and revocation."""
from datetime import datetime,timedelta,timezone
from uuid import uuid4
import psycopg
from fastapi.testclient import TestClient
from roboport_api.main import app
from roboport_api.session_tokens import new_token,token_digest
from test_pg_baseline import DSN


def test_account_ticket_list_live_two_users_and_revocation(monkeypatch):
    monkeypatch.setenv("ROBOport_SYNTHETIC_ACCESS_API","1")
    monkeypatch.setenv("ROBOport_DATABASE_URL",DSN)
    a,b,ca,cb,ta,tb,sa,sb,ea,eb,aa,ab=(uuid4() for _ in range(12))
    tokens=(new_token(),new_token())
    now=datetime.now(timezone.utc)
    with psycopg.connect(DSN) as db:
        for uid in (a,b):
            db.execute("INSERT INTO app_user(id,normalized_login) VALUES (%s,%s)",(uid,uid.hex))
            db.execute("INSERT INTO user_role(id,user_id,role_code) VALUES (%s,%s,'CLIENT')",(uuid4(),uid))
        for cid in (ca,cb):
            db.execute("INSERT INTO customer(id,label) VALUES (%s,%s)",(cid,cid.hex))
        for t,cid in ((ta,ca),(tb,cb)):
            db.execute("INSERT INTO service_ticket(id,customer_id) VALUES (%s,%s)",(t,cid))
        for u,cid in ((a,ca),(b,cb)):
            db.execute("INSERT INTO account_customer_link(user_id,customer_id) VALUES (%s,%s)",(u,cid))
        for e,t in ((ea,ta),(eb,tb)):
            db.execute("""INSERT INTO estimate(id,ticket_id,version,state,total_minor,currency)
              VALUES(%s,%s,1,'SENT',1500,'RUB')""",(e,t))
        for att,t,u in ((aa,ta,a),(ab,tb,b)):
            db.execute("""INSERT INTO attachment
              (id,ticket_id,object_key,mime,byte_size,sha256,upload_state,quarantine_state,created_by)
              VALUES (%s,%s,%s,'application/pdf',123,%s,'READY','CLEAR',%s)""",
              (att,t,att.hex,att.hex+att.hex,u))
        for sid,u,token in ((sa,a,tokens[0]),(sb,b,tokens[1])):
            db.execute("""INSERT INTO user_session
              (id,user_id,token_hash,csrf_hash,created_at,idle_expires_at,absolute_expires_at)
              VALUES(%s,%s,%s,%s,%s,%s,%s)""",
              (sid,u,token_digest(token),token_digest(new_token()),now,
               now+timedelta(minutes=30),now+timedelta(hours=4)))
    http=TestClient(app,base_url="https://127.0.0.1")
    url="/api/v1/dev/account/tickets"
    summary="/api/v1/dev/account/summary/"
    assert http.get(summary+"robots").status_code==401
    assert http.get(summary+"estimates").status_code==401
    assert http.get(summary+"documents").status_code==401
    assert http.get(url,headers={"X-User-ID":str(a),"X-Customer-ID":str(ca)}).status_code==401
    http.cookies.set("rp_session",tokens[0])
    first=http.get(url)
    assert first.status_code==200,first.text
    assert [x["id"] for x in first.json()["items"]]==[str(ta)]
    assert http.get(summary+"robots").json()["items"]==[]
    estimates=http.get(summary+"estimates")
    assert estimates.status_code==200 and [x["id"] for x in estimates.json()["items"]]==[str(ea)]
    documents=http.get(summary+"documents")
    assert documents.status_code==200 and [x["id"] for x in documents.json()["items"]]==[str(aa)]
    assert "object_key" not in str(documents.json())
    assert all(str(tb) not in str(v) for v in first.json()["items"])
    http.cookies.clear()
    http.cookies.set("rp_session",tokens[1])
    second=http.get(url)
    assert second.status_code==200 and [x["id"] for x in second.json()["items"]]==[str(tb)]
    assert [x["id"] for x in http.get(summary+"estimates").json()["items"]]==[str(eb)]
    assert [x["id"] for x in http.get(summary+"documents").json()["items"]]==[str(ab)]
    with psycopg.connect(DSN) as db:
        db.execute("UPDATE account_customer_link SET revoked_at=now() WHERE user_id=%s",(b,))
    assert http.get(url).json()["items"]==[]
    for section in ("robots","estimates","documents"):
        assert http.get(summary+section).json()["items"]==[]
    with psycopg.connect(DSN) as db:
        db.execute("UPDATE user_session SET revoked_at=now() WHERE id=%s",(sb,))
    assert http.get(url).status_code==401
    assert http.get(url,headers={"X-User-ID":str(a)}).status_code==401
    assert http.get(url+"?limit=51").status_code==422
    monkeypatch.delenv("ROBOport_SYNTHETIC_ACCESS_API")
    assert http.get(url).status_code==404


def test_spa_csrf_rotation_and_real_logout(monkeypatch):
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTH_API","1")
    monkeypatch.setenv("ROBOport_DATABASE_URL",DSN)
    user,sid=uuid4(),uuid4()
    token,old_csrf=new_token(),new_token()
    now=datetime.now(timezone.utc)
    with psycopg.connect(DSN) as db:
        db.execute("INSERT INTO app_user(id,normalized_login) VALUES(%s,%s)",(user,user.hex))
        db.execute("""INSERT INTO user_session
          (id,user_id,token_hash,csrf_hash,created_at,idle_expires_at,absolute_expires_at)
          VALUES (%s,%s,%s,%s,%s,%s,%s)""",
          (sid,user,token_digest(token),token_digest(old_csrf),now,
           now+timedelta(minutes=30),now+timedelta(hours=4)))
    http=TestClient(app,base_url="https://127.0.0.1")
    url="/api/v1/auth/csrf"
    assert http.post(url).status_code==403
    http.cookies.set("rp_session",token)
    assert http.post(url).status_code==403
    headers={"Origin":"https://127.0.0.1"}
    fresh=http.post(url,headers=headers)
    assert fresh.status_code==200,fresh.text
    csrf=fresh.json()["csrfToken"]
    assert len(csrf)==43 and csrf!=old_csrf
    # Old token may never be used for an authenticated mutation after rotation.
    logout="/api/v1/auth/logout"
    assert http.post(logout,headers={**headers,"X-CSRF-Token":old_csrf}).status_code==403
    assert http.post(logout,headers={**headers,"X-CSRF-Token":csrf}).status_code==200
    assert http.post(url,headers=headers).status_code==401
    assert http.get("/api/v1/auth/me").status_code==401
