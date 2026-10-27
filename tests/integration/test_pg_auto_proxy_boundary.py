"""PG17: spoofed forwarding headers cannot mint a synthetic proof or audit."""
import psycopg
import pytest
from fastapi.testclient import TestClient

from roboport_api.main import app
from test_pg_auto_vehicle_claim_http import _scenario
from test_pg_baseline import DSN


@pytest.mark.parametrize("spoof", [
    {"X-Forwarded-For": "127.0.0.1"},
    {"Forwarded": "for=127.0.0.1;proto=https"},
    {"X-Forwarded-Proto": "https"},
    {"X-Forwarded-Host": "localhost"},
    {"X-Real-IP": "127.0.0.1"},
])
def test_forged_proxy_metadata_does_not_issue_or_audit_proof(monkeypatch, spoof):
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTH_API", "1")
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTO_API", "1")
    monkeypatch.setenv("ROBOport_DATABASE_URL", DSN)
    d = _scenario()
    client = TestClient(app, base_url="https://127.0.0.1")
    client.cookies.set("rp_session", d["tokens"][d["intake"]])
    path = f"/api/v1/dev/auto/links/{d['link']}/issue-proof"
    basic = {
        "Origin": "https://127.0.0.1",
        "X-CSRF-Token": d["csrf"][d["intake"]],
    }
    forbidden = client.post(path, headers={**basic, **spoof})
    assert forbidden.status_code == 404 and forbidden.json()["code"] == "NOT_FOUND"
    with psycopg.connect(DSN) as db:
        assert db.execute(
            "SELECT count(*) FROM auto_vehicle_claim WHERE link_id=%s",
            (d["link"],),
        ).fetchone() == (0,)
        assert db.execute(
            "SELECT count(*) FROM auto_vehicle_claim_audit WHERE link_id=%s",
            (d["link"],),
        ).fetchone() == (0,)

    accepted = client.post(path, headers=basic)
    assert accepted.status_code == 200, accepted.text
    assert len(accepted.json()["proof"]) >= 40
    with psycopg.connect(DSN) as db:
        assert db.execute(
            "SELECT count(*) FROM auto_vehicle_claim_audit WHERE link_id=%s",
            (d["link"],),
        ).fetchone() == (1,)


def test_only_auto_routes_reject_forwarded_headers_and_scope_stays_dev(monkeypatch):
    # Header filtering is limited to /api/v1/dev/auto, not a general
    # backwards-incompatible behavior change for unrelated API endpoints.
    http = TestClient(app, base_url="https://127.0.0.1")
    assert http.get("/api/v1/meta", headers={"Forwarded": "for=203.0.113.1"}).status_code == 200
    monkeypatch.delenv("ROBOport_SYNTHETIC_AUTO_API", raising=False)
    assert http.get("/api/v1/dev/auto/vehicles").status_code == 404
