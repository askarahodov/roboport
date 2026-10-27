"""PG17 + HTTPS: повтор proof/review без повторных попыток и audit."""
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient

from roboport_api.main import app
from test_pg_auto_vehicle_claim_http import _scenario, _act
from test_pg_baseline import DSN


def _flags(monkeypatch):
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTO_API", "1")
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTH_API", "1")
    monkeypatch.setenv("ROBOport_DATABASE_URL", DSN)


def _post(d, actor, path, payload, key=None):
    http = TestClient(app, base_url="https://127.0.0.1")
    http.cookies.set("rp_session", d["tokens"][actor])
    headers = {"Origin": "https://127.0.0.1", "X-CSRF-Token": d["csrf"][actor]}
    if key is not None:
        headers["Idempotency-Key"] = key
    return http.post(path, json=payload, headers=headers)


def _issued(d):
    link = d["link"]
    issued = _post(d, d["intake"], f"/api/v1/dev/auto/links/{link}/issue-proof", {})
    assert issued.status_code == 200, issued.text
    return issued.json()["claimId"], issued.json()["proof"]


def test_redeem_retry_no_brute_budget_leak_and_review_retry(monkeypatch):
    _flags(monkeypatch)
    d = _scenario()
    claim, proof = _issued(d)
    redeem = f"/api/v1/dev/auto/claims/{claim}/redeem"
    review = f"/api/v1/dev/auto/claims/{claim}/review"
    wrong = "x" * 43
    wrong_key, proof_key, review_key = (uuid4().hex for _ in range(3))
    rejected = _post(d, d["client_a"], redeem, {"proof": wrong}, wrong_key)
    assert rejected.status_code == 200 and rejected.json() == {"status": "REJECTED"}
    again = _post(d, d["client_a"], redeem, {"proof": wrong}, wrong_key)
    assert again.status_code == 200 and again.json() == rejected.json()
    different = _post(d, d["client_a"], redeem, {"proof": proof}, wrong_key)
    assert different.status_code == 409
    assert different.json()["code"] == "CONFLICT"
    with psycopg.connect(DSN) as db:
        assert db.execute("SELECT proof_attempts FROM auto_vehicle_claim WHERE id=%s",
                          (claim,)).fetchone()[0] == 1
        assert db.execute("""SELECT count(*) FROM auto_vehicle_claim_audit
                      WHERE claim_id=%s AND action='PROOF_REJECTED'""",
                          (claim,)).fetchone()[0] == 1

    accepted = _post(d, d["client_a"], redeem, {"proof": proof}, proof_key)
    assert accepted.status_code == 200 and accepted.json()["status"] == "PENDING_REVIEW"
    assert _post(d, d["client_a"], redeem, {"proof": proof}, proof_key).json() == accepted.json()
    with psycopg.connect(DSN) as db:
        assert db.execute("SELECT proof_attempts FROM auto_vehicle_claim WHERE id=%s",
                          (claim,)).fetchone()[0] == 2
        assert db.execute("""SELECT count(*) FROM auto_vehicle_claim_audit
                      WHERE claim_id=%s AND action='PROOF_ACCEPTED'""",
                          (claim,)).fetchone()[0] == 1

    reviewed = _post(d, d["manager"], review, {"approve": True}, review_key)
    assert reviewed.status_code == 200 and reviewed.json()["status"] == "VERIFIED"
    assert _post(d, d["manager"], review, {"approve": True}, review_key).json() == reviewed.json()
    assert _post(d, d["manager"], review, {"approve": False}, review_key).status_code == 409
    # Финальное VERIFIED состояние не разрешает снова предъявлять proof: 403.
    assert _post(d, d["client_a"], redeem, {"proof": proof}, proof_key).status_code == 403
    # Старый endpoint без ключа по-прежнему single-use.
    assert _post(d, d["manager"], review, {"approve": True}).status_code == 403
    with psycopg.connect(DSN) as db:
        assert db.execute("""SELECT count(*) FROM auto_vehicle_claim_audit
                      WHERE claim_id=%s AND action='VERIFIED'""",
                          (claim,)).fetchone()[0] == 1
        rows = db.execute("""SELECT operation,outcome,key_hash,request_fingerprint
                FROM auto_claim_retry_receipt WHERE claim_id=%s""", (claim,)).fetchall()
        assert len(rows) == 3
        assert all(len(key_hash) == 64 and len(fingerprint) == 64
                   for _, _, key_hash, fingerprint in rows)
        assert proof not in str(rows) and wrong not in str(rows)
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("DELETE FROM auto_claim_retry_receipt WHERE claim_id=%s", (claim,))


def test_live_revocations_disallow_successful_replays(monkeypatch):
    _flags(monkeypatch)
    d = _scenario()
    claim, proof = _issued(d)
    redeem = f"/api/v1/dev/auto/claims/{claim}/redeem"
    review = f"/api/v1/dev/auto/claims/{claim}/review"
    client_key, manager_key = uuid4().hex, uuid4().hex
    assert _post(d, d["client_a"], redeem, {"proof": proof}, client_key).status_code == 200
    # Customer entitlement withdrawn: receipt must not bypass revocation.
    with psycopg.connect(DSN) as db:
        db.execute("""UPDATE account_customer_link SET revoked_at=now()
                      WHERE user_id=%s""", (d["client_a"],))
    assert _post(d, d["client_a"], redeem, {"proof": proof}, client_key).status_code == 403
    assert _post(d, d["manager"], review, {"approve": True}, manager_key).status_code == 403


def test_review_replay_requires_live_manager_scope_and_link(monkeypatch):
    _flags(monkeypatch)
    d = _scenario()
    claim, proof = _issued(d)
    assert _post(d, d["client_a"], f"/api/v1/dev/auto/claims/{claim}/redeem",
                 {"proof": proof}).status_code == 200
    review = f"/api/v1/dev/auto/claims/{claim}/review"
    key = uuid4().hex
    assert _post(d, d["manager"], review, {"approve": False}, key).json() == {"status":"DECLINED"}
    assert _post(d, d["manager"], review, {"approve": False}, key).status_code == 200
    with psycopg.connect(DSN) as db:
        db.execute("""UPDATE auto_vehicle_staff_assignment SET revoked_at=now()
                      WHERE vehicle_id=%s AND user_id=%s AND role_code='MANAGER'""",
                   (d["vehicle"], d["manager"]))
    assert _post(d, d["manager"], review, {"approve": False}, key).status_code == 403


def test_attempt_budget_five_distinct_keys_and_no_duplicate_audit(monkeypatch):
    _flags(monkeypatch)
    d = _scenario()
    claim, _ = _issued(d)
    endpoint = f"/api/v1/dev/auto/claims/{claim}/redeem"
    key = uuid4().hex
    for i in range(5):
        active_key = key if i == 0 else uuid4().hex
        response = _post(d, d["client_a"], endpoint, {"proof": "z" * 43}, active_key)
        assert response.status_code == 200 and response.json()["status"] == "REJECTED"
        if i == 0:
            assert _post(d, d["client_a"], endpoint, {"proof": "z" * 43}, key).status_code == 200
    assert _post(d, d["client_a"], endpoint, {"proof": "z" * 43}, uuid4().hex).status_code == 403
    with psycopg.connect(DSN) as db:
        assert db.execute("SELECT proof_attempts FROM auto_vehicle_claim WHERE id=%s",
                          (claim,)).fetchone()[0] == 5
        assert db.execute("""SELECT count(*) FROM auto_vehicle_claim_audit
                       WHERE claim_id=%s AND action='PROOF_REJECTED'""",
                          (claim,)).fetchone()[0] == 5


def test_concurrent_same_key_review_is_single_decision(monkeypatch):
    _flags(monkeypatch)
    d = _scenario()
    claim, proof = _issued(d)
    assert _post(
        d, d["client_a"], f"/api/v1/dev/auto/claims/{claim}/redeem",
        {"proof": proof},
    ).status_code == 200
    review = f"/api/v1/dev/auto/claims/{claim}/review"
    key = uuid4().hex

    def attempt(_):
        response = _post(d, d["manager"], review, {"approve": True}, key)
        return response.status_code, response.json()

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(attempt, range(2)))
    assert responses == [(200, {"status": "VERIFIED"})] * 2
    with psycopg.connect(DSN) as db:
        assert db.execute(
            """SELECT count(*) FROM auto_vehicle_claim_audit
               WHERE claim_id=%s AND action='VERIFIED'""",
            (claim,),
        ).fetchone()[0] == 1
        assert db.execute(
            """SELECT count(*) FROM auto_claim_retry_receipt
               WHERE claim_id=%s AND operation='review'""",
            (claim,),
        ).fetchone()[0] == 1
