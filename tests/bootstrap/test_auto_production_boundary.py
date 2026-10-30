"""Production release boundary: no synthetic toggles can authorize public auto."""
import pytest
from fastapi.testclient import TestClient

from roboport_api.main import app


@pytest.mark.parametrize("flags", [
    {},
    {"ROBOport_SYNTHETIC_AUTO_API": "1",
     "ROBOport_SYNTHETIC_AUTH_API": "1"},
    {"ROBOport_SYNTHETIC_AUTO_API": "1",
     "ROBOport_SYNTHETIC_AUTH_API": "1",
     "ROBOport_SYNTHETIC_AUTO_BRANCH_SCOPE": "1"},
])
def test_release_readiness_fails_closed_even_with_dev_flags(monkeypatch, flags):
    for key in ("ROBOport_SYNTHETIC_AUTO_API", "ROBOport_SYNTHETIC_AUTH_API",
                "ROBOport_SYNTHETIC_AUTO_BRANCH_SCOPE"):
        monkeypatch.delenv(key, raising=False)
    for key, value in flags.items():
        monkeypatch.setenv(key, value)
    for base in ("http://127.0.0.1", "https://127.0.0.1",
                 "https://service.example"):
        client = TestClient(app, base_url=base)
        ready = client.get("/health/ready")
        assert ready.status_code == 503
        assert ready.json() == {"status": "not_ready"}


def test_auto_dev_paths_hidden_from_public_openapi():
    schema = TestClient(app).get("/openapi.json").json()
    assert not any(path.startswith("/api/v1/dev/auto/")
                   for path in schema["paths"])
    assert not any("/organizations/" in path and "/auto/" in path
                   for path in schema["paths"])


@pytest.mark.parametrize("path", [
    "/api/v1/dev/auto/vehicles",
    "/api/v1/dev/auto/organizations/00000000-0000-0000-0000-000000000001",
    "/api/v1/dev/auto/organizations/00000000-0000-0000-0000-000000000001/locations/00000000-0000-0000-0000-000000000002/orders/00000000-0000-0000-0000-000000000003/overview",
])
def test_public_origin_cannot_reach_auto_dev_routes(monkeypatch, path):
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTO_API", "1")
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTH_API", "1")
    result = TestClient(app, base_url="https://service.example").get(path)
    assert result.status_code == 404
    assert result.json()["code"] == "NOT_FOUND"
    assert result.headers["cache-control"] == "no-store"
