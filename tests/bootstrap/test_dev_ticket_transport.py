"""Every opt-in ticket route requires HTTPS on loopback, ignoring forwarded headers."""
import pytest
from fastapi.testclient import TestClient
from roboport_api.main import app

ROUTES=(
    "/api/v1/dev/tickets",
    "/api/v1/dev/tickets/keyset",
    "/api/v1/dev/tickets/11111111-1111-4111-8111-111111111111",
    "/api/v1/dev/tickets/11111111-1111-4111-8111-111111111111/state",
    "/api/v1/dev/tickets/11111111-1111-4111-8111-111111111111/access",
)

@pytest.mark.parametrize("path", ROUTES)
@pytest.mark.parametrize("base_url", (
    "http://127.0.0.1",
    "http://localhost",
    "https://testserver",
    "https://example.com",
))
def test_insecure_or_nonloopback_ticket_request_fails_closed(monkeypatch, path, base_url):
    monkeypatch.setenv("ROBOport_SYNTHETIC_ACCESS_API","1")
    monkeypatch.setenv("ROBOport_DATABASE_URL","postgresql://u:p@127.0.0.1:1/nonexistent")
    client=TestClient(app,base_url=base_url)
    response=client.get(path,headers={
        "X-Forwarded-Proto":"https",
        "X-Forwarded-Host":"127.0.0.1",
        "X-User-ID":"11111111-1111-4111-8111-111111111111",
    },cookies={"rp_session":"x"*43})
    assert response.status_code==404, (path,base_url,response.text)
    assert response.headers["cache-control"]=="no-store"
    assert response.headers["x-request-id"]==response.json()["requestId"]

@pytest.mark.parametrize("path",ROUTES)
def test_https_loopback_without_session_remains_denied(monkeypatch,path):
    monkeypatch.setenv("ROBOport_SYNTHETIC_ACCESS_API","1")
    monkeypatch.setenv("ROBOport_DATABASE_URL","postgresql://u:p@127.0.0.1:1/nonexistent")
    response=TestClient(app,base_url="https://localhost").get(path)
    assert response.status_code==401

def test_http_transport_does_not_change_public_catalog():
    client=TestClient(app,base_url="http://testserver")
    assert client.get("/api/v1/catalog/brands").status_code==200
    assert client.get("/health/live").status_code==200
