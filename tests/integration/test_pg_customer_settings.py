"""Real PostgreSQL CSRF bootstrap and private notification preferences."""
from datetime import datetime,timedelta,timezone
from uuid import uuid4
import psycopg
from fastapi.testclient import TestClient
from roboport_api.main import app
from roboport_api.session_tokens import new_token,token_digest
from test_pg_baseline import DSN


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


def test_in_app_settings_owner_csrf_audit_and_revocation(monkeypatch):
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTH_API","1")
    monkeypatch.setenv("ROBOport_SYNTHETIC_ACCESS_API","1")
    monkeypatch.setenv("ROBOport_DATABASE_URL",DSN)
    users=[uuid4(),uuid4()]
    tokens=[new_token(),new_token()]
    secrets=[new_token(),new_token()]
    now=datetime.now(timezone.utc)
    with psycopg.connect(DSN) as db:
        for uid,raw,csrf in zip(users,tokens,secrets):
            db.execute("INSERT INTO app_user(id,normalized_login) VALUES(%s,%s)",(uid,uid.hex))
            db.execute("INSERT INTO user_role(id,user_id,role_code) VALUES(%s,%s,'CLIENT')",(uuid4(),uid))
            db.execute("""INSERT INTO user_session(id,user_id,token_hash,csrf_hash,
              created_at,idle_expires_at,absolute_expires_at)
              VALUES (%s,%s,%s,%s,%s,%s,%s)""",
              (uuid4(),uid,token_digest(raw),token_digest(csrf),now,
               now+timedelta(minutes=30),now+timedelta(hours=4)))
    http=TestClient(app,base_url="https://127.0.0.1")
    url="/api/v1/dev/account/preferences"
    headers={"Origin":"https://127.0.0.1"}
    assert http.get(url).status_code==401
    http.cookies.set("rp_session",tokens[0])
    assert http.get(url).json()=={"inAppEnabled":True}
    assert http.post(url,json={"inAppEnabled":False}).status_code==403
    assert http.post(url,headers=headers,json={"inAppEnabled":False}).status_code==403
    res=http.post(url,headers={**headers,"X-CSRF-Token":secrets[0]},
                  json={"inAppEnabled":False})
    assert res.status_code==200,res.text
    assert http.get(url).json()=={"inAppEnabled":False}
    # Idempotent same-value replay must not duplicate audit events.
    assert http.post(url,headers={**headers,"X-CSRF-Token":secrets[0]},
                     json={"inAppEnabled":False}).status_code==200
    with psycopg.connect(DSN) as db:
        assert db.execute("SELECT count(*) FROM customer_preference_audit WHERE user_id=%s",
                          (users[0],)).fetchone()[0]==1
    http.cookies.clear()
    http.cookies.set("rp_session",tokens[1])
    assert http.get(url).json()=={"inAppEnabled":True}
    assert http.post(url,headers={**headers,"X-CSRF-Token":secrets[0]},
                     json={"inAppEnabled":False}).status_code==403
    with psycopg.connect(DSN) as db:
        db.execute("UPDATE user_session SET revoked_at=now() WHERE user_id=%s",(users[1],))
    assert http.get(url).status_code==401
    assert http.post(url,headers={**headers,"X-CSRF-Token":secrets[1]},
                     json={"inAppEnabled":False}).status_code==401
