from fastapi.testclient import TestClient
from roboport_api.main import app

client = TestClient(app)

def test_liveness():
    assert client.get("/health/live").json()["status"] == "alive"

def test_readiness_fails_closed():
    assert client.get("/health/ready").status_code == 503

def test_private_endpoint_not_implemented():
    assert client.get("/api/v1/customer/me").status_code == 404
