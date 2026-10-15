"""Fail-closed tests for synthetic authentication's DB boundary."""
from fastapi.testclient import TestClient
from roboport_api.main import app


def test_synthetic_auth_rejects_malformed_and_remote_database_urls(monkeypatch):
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTH_API","1")
    client=TestClient(app,base_url="https://127.0.0.1")
    for dsn in (
        "postgresql://[bad",
        "postgresql://127.0.0.1:invalid/example",
        "postgresql://remote.example:5432/example",
        "postgresql://127.0.0.1@remote.example:5432/example",
        "http://127.0.0.1:5432/example",
        "postgresql://127.0.0.1/example",
    ):
        monkeypatch.setenv("ROBOport_DATABASE_URL",dsn)
        response=client.post("/api/v1/auth/login",headers={"Origin":"https://127.0.0.1"},json={})
        assert response.status_code==403,(dsn,response.status_code)
        assert "set-cookie" not in response.headers
