from fastapi.testclient import TestClient
from roboport_api.main import app

client = TestClient(app)

def test_catalog_is_read_only_and_synthetic():
    assert client.get("/api/v1/catalog/brands").json() == {"items": []}
    assert client.get("/api/v1/catalog/models").json() == {"items": []}
    assert client.get("/api/v1/catalog/models/unavailable").status_code == 404
    assert client.post("/api/v1/catalog/brands", json={}).status_code == 405
