"""Negative HTTP parsing cases must not raise an exception or issue cookies."""
from fastapi.testclient import TestClient
from roboport_api.main import app


def test_malformed_json_fails_closed_before_auth_db(monkeypatch):
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTH_API","1")
    monkeypatch.setenv("ROBOport_DATABASE_URL","postgresql://x:y@127.0.0.1:5432/x")
    client=TestClient(app,base_url="https://127.0.0.1")
    for body in ('{', 'null', '[]', '"literal"', '{"login":null}'):
        response=client.post("/api/v1/auth/login",content=body,
            headers={"Origin":"https://127.0.0.1","Content-Type":"application/json"})
        assert response.status_code==422,(body,response.status_code)
        assert "set-cookie" not in response.headers
