"""Synthetic development routes reject unsafe transport before accessing backends."""
from uuid import UUID
import pytest
from fastapi.testclient import TestClient
from roboport_api.main import app

PATHS = (
    "/api/v1/dev/tickets",
    "/api/v1/dev/tickets/keyset",
    "/api/v1/dev/tickets/11111111-1111-4111-8111-111111111111/state",
    "/api/v1/dev/authorized/tickets/11111111-1111-4111-8111-111111111111",
    "/api/v1/dev/attachments/11111111-1111-4111-8111-111111111111/content",
    "/api/v1/dev/estimates/11111111-1111-4111-8111-111111111111/access",
)

@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize("base_url", ("http://127.0.0.1", "https://remote.example", "http://remote.example"))
def test_unsafe_dev_gets_fail_closed_before_handlers(monkeypatch, path, base_url):
    monkeypatch.setenv("ROBOport_SYNTHETIC_ACCESS_API", "1")
    monkeypatch.setenv("ROBOport_DATABASE_URL", "postgresql://user:pass@127.0.0.1:1/no_server")
    client = TestClient(app, base_url=base_url)
    response=client.get(path)
    assert response.status_code == 404, (base_url,path,response.text)
    assert response.headers["cache-control"] == "no-store"
    assert UUID(response.headers["x-request-id"])
    assert response.json()["requestId"] == response.headers["x-request-id"]

@pytest.mark.parametrize("base_url", ("http://127.0.0.1", "https://remote.example"))
def test_unsafe_dev_post_rejected_before_request_body(monkeypatch, base_url):
    monkeypatch.setenv("ROBOport_SYNTHETIC_ACCESS_API", "1")
    response=TestClient(app,base_url=base_url).post(
        "/api/v1/dev/tickets/11111111-1111-4111-8111-111111111111/attachments",
        content=b"test",headers={"content-type":"application/pdf"})
    assert response.status_code==404
    assert response.headers["cache-control"]=="no-store"

def test_namespace_guard_exact_segment_not_prefix(monkeypatch):
    monkeypatch.setenv("ROBOport_SYNTHETIC_ACCESS_API","1")
    client=TestClient(app,base_url="http://127.0.0.1")
    assert client.get("/api/v1/dev").status_code==404
    assert client.get("/api/v1/devil").status_code==404
    assert client.get("/api/v1/catalog/brands").status_code==200

def test_all_private_dev_routes_hidden_from_openapi():
    paths=TestClient(app).get("/openapi.json").json()["paths"]
    assert all(not path.startswith("/api/v1/dev/") for path in paths)
