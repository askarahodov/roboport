"""Synthetic HTTPS-loopback login flow on real PostgreSQL; no public user accounts."""
from uuid import uuid4
import psycopg
from fastapi.testclient import TestClient

from roboport_api.main import app
from roboport_api.passwords import hash_password
from test_pg_baseline import DSN

ORIGIN="https://127.0.0.1"


def _user(login, password="Synthetic-valid-passphrase-2026!"):
    uid=uuid4()
    with psycopg.connect(DSN) as conn:
        conn.execute("INSERT INTO app_user(id,normalized_login) VALUES(%s,%s)",(uid,login))
        conn.execute("INSERT INTO local_credential(user_id,password_hash,must_change_password) VALUES(%s,%s,false)",(uid,hash_password(password)))
    return uid


def test_login_me_logout_cookie_and_csrf(monkeypatch):
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTH_API","1")
    monkeypatch.setenv("ROBOport_DATABASE_URL",DSN)
    uid=_user("login-http-1")
    c=TestClient(app,base_url=ORIGIN)
    assert c.post("/api/v1/auth/login",json={"login":"login-http-1","password":"Synthetic-valid-passphrase-2026!"}).status_code==403
    response=c.post("/api/v1/auth/login",json={"login":"login-http-1","password":"Synthetic-valid-passphrase-2026!"},headers={"Origin":ORIGIN})
    assert response.status_code==200, response.text
    csrf=response.json()["csrfToken"]
    assert "HttpOnly" in response.headers["set-cookie"]
    assert "Secure" in response.headers["set-cookie"]
    assert c.get("/api/v1/auth/me").json()=={"subjectId":str(uid)}
    assert c.post("/api/v1/auth/logout",headers={"Origin":ORIGIN}).status_code==403
    assert c.post("/api/v1/auth/logout",headers={"Origin":ORIGIN,"X-CSRF-Token":"bad"}).status_code==403
    assert c.post("/api/v1/auth/logout",headers={"Origin":ORIGIN,"X-CSRF-Token":csrf}).status_code==200
    assert c.get("/api/v1/auth/me").status_code==401
    assert c.post("/api/v1/auth/logout",headers={"Origin":ORIGIN,"X-CSRF-Token":csrf}).status_code==403


def test_login_password_failures_are_rate_limited(monkeypatch):
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTH_API","1")
    monkeypatch.setenv("ROBOport_DATABASE_URL",DSN)
    _user("login-http-throttled")
    c=TestClient(app,base_url=ORIGIN)
    for _ in range(5):
        response=c.post("/api/v1/auth/login",json={"login":"login-http-throttled","password":"wrong!"},headers={"Origin":ORIGIN})
        assert response.status_code==401, response.text
    assert c.post("/api/v1/auth/login",json={"login":"login-http-throttled","password":"Synthetic-valid-passphrase-2026!"},headers={"Origin":ORIGIN}).status_code==429


def test_auth_disabled_by_default_even_with_credentials(monkeypatch):
    monkeypatch.delenv("ROBOport_SYNTHETIC_AUTH_API",raising=False)
    c=TestClient(app,base_url=ORIGIN)
    assert c.post("/api/v1/auth/login",json={"login":"foo","password":"irrelevant"},headers={"Origin":ORIGIN}).status_code==403
