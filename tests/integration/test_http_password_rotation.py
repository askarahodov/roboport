"""Password rotation and revocation regression on synthetic PostgreSQL."""
import psycopg
from fastapi.testclient import TestClient
from test_pg_baseline import DSN
from test_http_login_flow import _user, ORIGIN
from roboport_api.main import app


def test_password_rotation_revokes_session(monkeypatch):
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTH_API","1")
    monkeypatch.setenv("ROBOport_DATABASE_URL",DSN)
    _user("rotation-sample")
    client=TestClient(app,base_url=ORIGIN)
    before=client.post("/api/v1/auth/login",json={
        "login":"rotation-sample","password":"Synthetic-valid-passphrase-2026!"
    },headers={"Origin":ORIGIN})
    assert before.status_code==200
    csrf=before.json()["csrfToken"]
    change=client.post("/api/v1/auth/change-password",json={
        "oldPassword":"Synthetic-valid-passphrase-2026!",
        "newPassword":"Different-synthetic-passphrase-2026!"
    },headers={"Origin":ORIGIN,"X-CSRF-Token":csrf})
    assert change.status_code==200,change.text
    assert client.get("/api/v1/auth/me").status_code==401
    old=client.post("/api/v1/auth/login",json={
        "login":"rotation-sample","password":"Synthetic-valid-passphrase-2026!"
    },headers={"Origin":ORIGIN})
    assert old.status_code==401
    fresh=client.post("/api/v1/auth/login",json={
        "login":"rotation-sample","password":"Different-synthetic-passphrase-2026!"
    },headers={"Origin":ORIGIN})
    assert fresh.status_code==200,fresh.text
