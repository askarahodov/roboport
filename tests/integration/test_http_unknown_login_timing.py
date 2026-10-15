"""Synthetic login must verify Argon2id even if the account is absent."""
from fastapi.testclient import TestClient
from roboport_api import auth_http
from roboport_api.main import app
from test_pg_baseline import DSN


def test_unknown_account_runs_password_verification(monkeypatch):
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTH_API","1")
    monkeypatch.setenv("ROBOport_DATABASE_URL",DSN)
    observed=[]
    actual=auth_http.verify_password

    def observe(password, encoded_hash):
        observed.append(encoded_hash)
        return actual(password,encoded_hash)

    monkeypatch.setattr(auth_http,"verify_password",observe)
    client=TestClient(app,base_url="https://127.0.0.1")
    response=client.post("/api/v1/auth/login",
        json={"login":"nonexistent-synthetic-login","password":"Wrong-synthetic-password-2026!"},
        headers={"Origin":"https://127.0.0.1"})
    assert response.status_code==401
    assert len(observed)==1
    assert observed[0].startswith("$argon2id$")
    assert "set-cookie" not in response.headers
