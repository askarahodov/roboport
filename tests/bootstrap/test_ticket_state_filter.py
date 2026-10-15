"""Reject unsupported ticket status filters before opening a database connection."""
from fastapi.testclient import TestClient
from roboport_api.main import app
from roboport_api.ticket_read import TICKET_STATES
from pathlib import Path
import ast


def test_states_match_database_migration():
    source = (Path(__file__).resolve().parents[2] / "apps/api/alembic/versions/0001_core.py").read_text()
    module = ast.parse(source)
    declaration = next(n for n in module.body if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "STATES" for t in n.targets))
    assert set(ast.literal_eval(declaration.value)) == TICKET_STATES


def test_state_filter_rejected_before_db_connect(monkeypatch):
    monkeypatch.setenv("ROBOport_SYNTHETIC_ACCESS_API", "1")
    monkeypatch.setenv("ROBOport_DATABASE_URL", "postgresql://u:p@127.0.0.1:1/nonexistent")
    client = TestClient(app)
    for state in ("invalid", "draft", "DRAFT%27", "COMPLETED,REPAIRING"):
        response = client.get("/api/v1/dev/tickets", params={"state": state})
        assert response.status_code == 422, (state, response.text)
        assert response.headers["cache-control"] == "no-store"
    assert client.get("/api/v1/dev/tickets", params={"limit": 0}).status_code == 422
    assert client.get("/api/v1/dev/tickets", params={"offset": 10001}).status_code == 422


def test_dev_list_remains_disabled(monkeypatch):
    monkeypatch.delenv("ROBOport_SYNTHETIC_ACCESS_API", raising=False)
    assert TestClient(app).get("/api/v1/dev/tickets", params={"state": "bad"}).status_code == 404
