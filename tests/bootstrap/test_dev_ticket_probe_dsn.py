"""Development ticket access probe must never connect to malformed or remote DB."""
from fastapi.testclient import TestClient
from roboport_api.main import app


def test_dev_ticket_probe_rejects_unsafe_database_urls(monkeypatch):
    monkeypatch.setenv("ROBOport_SYNTHETIC_ACCESS_API", "1")
    client = TestClient(app)
    for dsn in (
        "postgresql://[invalid",
        "postgresql://127.0.0.1:bad/roboport",
        "postgresql://user@remote.example:5432/roboport",
        "postgresql://127.0.0.1@remote.example:5432/roboport",
        "http://127.0.0.1:5432/roboport",
        "postgresql://127.0.0.1/roboport",
    ):
        monkeypatch.setenv("ROBOport_DATABASE_URL", dsn)
        response = client.get("/api/v1/dev/tickets/11111111-1111-4111-8111-111111111111/access")
        assert response.status_code == 404, (dsn, response.status_code)
        assert "accessible" not in response.text
        assert response.headers["cache-control"] == "no-store"
