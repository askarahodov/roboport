"""PG17: atomic cross-worker socket-peer quota for synthetic auto mutations."""
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import psycopg
from fastapi.testclient import TestClient

from roboport_api.auto_peer_throttle import peer_fingerprint
from roboport_api.main import app
from test_pg_auto_vehicle_claim_http import _scenario
from test_pg_baseline import DSN

_KEY = "synthetic-test-peer-hmac-" + "S" * 48


def _flags(monkeypatch):
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTH_API", "1")
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTO_API", "1")
    monkeypatch.setenv("ROBOport_DATABASE_URL", DSN)
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTO_PEER_LIMIT", "1")
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTO_PEER_HMAC_KEY", _KEY)


def _client(ip: str, port=42424):
    return TestClient(
        app, base_url="https://127.0.0.1", client=(ip, port),
    )


def _post(client, d, *, auth=False):
    client.cookies.set("rp_session", d["tokens"][d["intake"]])
    headers = (
        {"Origin": "https://127.0.0.1", "X-CSRF-Token": d["csrf"][d["intake"]]}
        if auth else {}
    )
    return client.post(
        f"/api/v1/dev/auto/links/{d['link']}/issue-proof",
        headers=headers,
    )


def test_peer_throttle_denial_persists_even_for_csrf_failures_and_window_reset(monkeypatch):
    _flags(monkeypatch)
    d = _scenario()
    client = _client("127.0.0.1")
    for _ in range(30):
        result = _post(client, d)
        assert result.status_code == 403, result.text
    denied = _post(client, d, auth=True)
    assert denied.status_code == 429 and denied.json()["code"] == "RATE_LIMITED"
    assert denied.headers["cache-control"] == "no-store"
    assert denied.headers["retry-after"] == "900"
    assert "proof" not in denied.json()
    tag = peer_fingerprint(("127.0.0.1", 1), _KEY)
    with psycopg.connect(DSN) as db:
        assert db.execute(
            "SELECT attempts FROM auto_peer_throttle WHERE peer_hmac=%s",
            (tag,),
        ).fetchone() == (31,)
        assert db.execute(
            "SELECT count(*) FROM auto_vehicle_claim WHERE link_id=%s",
            (d["link"],),
        ).fetchone() == (0,)
        assert db.execute(
            "SELECT count(*) FROM auto_vehicle_claim_audit WHERE link_id=%s",
            (d["link"],),
        ).fetchone() == (0,)
        # Only hashed socket address, no raw IP or session data in the row.
        rows = db.execute("SELECT * FROM auto_peer_throttle").fetchall()
        assert all("127.0.0.1" not in str(row) for row in rows)
        # The second loopback peer has an entirely independent quota.
    alternate = _post(_client("127.0.0.2"), d, auth=False)
    assert alternate.status_code == 403
    with psycopg.connect(DSN) as db:
        assert db.execute(
            "SELECT attempts FROM auto_peer_throttle WHERE peer_hmac=%s",
            (peer_fingerprint(("127.0.0.2", 1), _KEY),),
        ).fetchone() == (1,)
        db.execute(
            """UPDATE auto_peer_throttle
               SET window_started_at=now()-interval '16 minutes'
               WHERE peer_hmac=%s""",
            (tag,),
        )
    recovered = _post(client, d, auth=True)
    assert recovered.status_code == 200, recovered.text
    assert len(recovered.json()["proof"]) >= 40
    with psycopg.connect(DSN) as db:
        assert db.execute(
            "SELECT attempts FROM auto_peer_throttle WHERE peer_hmac=%s",
            (tag,),
        ).fetchone() == (1,)
        assert db.execute(
            "SELECT count(*) FROM auto_vehicle_claim_audit WHERE link_id=%s",
            (d["link"],),
        ).fetchone() == (1,)


def test_concurrent_requests_have_exact_single_atomic_ip_budget(monkeypatch):
    _flags(monkeypatch)
    d = _scenario()
    tag = peer_fingerprint(("::1", 5555), _KEY)

    def attempt(i):
        # Port changes must not bypass the shared socket-IP bucket.
        return _post(_client("::1", 6000 + i), d).status_code

    with ThreadPoolExecutor(max_workers=12) as pool:
        statuses = list(pool.map(attempt, range(38)))
    assert statuses.count(403) == 30
    assert statuses.count(429) == 8
    with psycopg.connect(DSN) as db:
        assert db.execute(
            "SELECT attempts FROM auto_peer_throttle WHERE peer_hmac=%s",
            (tag,),
        ).fetchone() == (31,)
        assert db.execute(
            "SELECT count(*) FROM auto_vehicle_claim WHERE link_id=%s",
            (d["link"],),
        ).fetchone() == (0,)


def test_already_issued_proof_and_replay_do_not_bypass_peer_quota(monkeypatch):
    _flags(monkeypatch)
    d = _scenario()
    http = _client("127.0.0.3")
    first = _post(http, d, auth=True)
    assert first.status_code == 200
    # Another POST with the same link is rejected by state, but still spends
    # socket quota. IP quota measures requests, not unique business events.
    replay = _post(http, d, auth=True)
    assert replay.status_code == 403
    with psycopg.connect(DSN) as db:
        assert db.execute(
            "SELECT attempts FROM auto_peer_throttle WHERE peer_hmac=%s",
            (peer_fingerprint(("127.0.0.3", 1), _KEY),),
        ).fetchone() == (2,)
