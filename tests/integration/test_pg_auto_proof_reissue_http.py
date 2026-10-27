"""Однократная замена потерянного proof: PostgreSQL17 + реальный HTTPS HTTP."""
from concurrent.futures import ThreadPoolExecutor
import hashlib

import psycopg
import pytest

from test_pg_auto_claim_retry_http import _flags, _post
from test_pg_auto_vehicle_claim_http import _scenario
from test_pg_baseline import DSN


def _issue(d):
    issued = _post(
        d, d["intake"], f"/api/v1/dev/auto/links/{d['link']}/issue-proof", {},
    )
    assert issued.status_code == 200, issued.text
    return issued.json()["claimId"], issued.json()["proof"]


def _reissue(d):
    return _post(
        d, d["intake"], f"/api/v1/dev/auto/links/{d['link']}/reissue-proof", {},
    )


def test_lost_response_reissue_invalidates_old_proof_without_mutating_claim(monkeypatch):
    _flags(monkeypatch)
    d = _scenario()
    claim, old_proof = _issue(d)

    # Даже если исходный ответ ISSUE потерян, link_id уже известен INTAKE.
    result = _reissue(d)
    assert result.status_code == 200, result.text
    assert result.json()["status"] == "REISSUED"
    assert result.json()["claimId"] == claim
    new_proof = result.json()["proof"]
    assert new_proof != old_proof and len(new_proof) >= 40
    assert result.headers["cache-control"] == "no-store"
    assert result.headers["pragma"] == "no-cache"
    assert _reissue(d).status_code == 403

    with psycopg.connect(DSN) as db:
        original = db.execute(
            """SELECT proof_hash,proof_attempts FROM auto_vehicle_claim WHERE id=%s""",
            (claim,),
        ).fetchone()
        assert original == (hashlib.sha256(old_proof.encode()).hexdigest(), 0)
        stored = db.execute(
            """SELECT proof_hash,issued_by FROM auto_vehicle_claim_reissue
               WHERE claim_id=%s""", (claim,),
        ).fetchone()
        assert stored == (hashlib.sha256(new_proof.encode()).hexdigest(), d["intake"])
        assert old_proof not in str(stored) and new_proof not in str(stored)
        assert db.execute(
            """SELECT action FROM auto_vehicle_claim_audit
               WHERE claim_id=%s ORDER BY created_at,id""", (claim,),
        ).fetchall() == [("ISSUED",), ("REISSUED",)]
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute(
                    "UPDATE auto_vehicle_claim_reissue SET issued_by=%s WHERE claim_id=%s",
                    (d["manager"], claim),
                )
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute(
                    "DELETE FROM auto_vehicle_claim_reissue WHERE claim_id=%s", (claim,),
                )

    endpoint = f"/api/v1/dev/auto/claims/{claim}/redeem"
    old = _post(d, d["client_a"], endpoint, {"proof": old_proof})
    assert old.status_code == 200 and old.json() == {"status": "REJECTED"}
    current = _post(d, d["client_a"], endpoint, {"proof": new_proof})
    assert current.status_code == 200 and current.json() == {"status": "PENDING_REVIEW"}
    review = _post(
        d, d["manager"], f"/api/v1/dev/auto/claims/{claim}/review",
        {"approve": True},
    )
    assert review.status_code == 200 and review.json() == {"status": "VERIFIED"}
    with psycopg.connect(DSN) as db:
        assert db.execute(
            "SELECT proof_attempts FROM auto_vehicle_claim WHERE id=%s", (claim,),
        ).fetchone()[0] == 2
        assert db.execute(
            """SELECT count(*) FROM auto_vehicle_claim_audit
               WHERE claim_id=%s AND action='REISSUED'""", (claim,),
        ).fetchone()[0] == 1


def test_no_reissue_after_attempt_or_claimant_and_unscoped_denied(monkeypatch):
    _flags(monkeypatch)
    d = _scenario()
    claim, original = _issue(d)
    path = f"/api/v1/dev/auto/links/{d['link']}/reissue-proof"
    assert _post(d, d["manager"], path, {}).status_code == 403
    assert _post(d, d["client_a"], path, {}).status_code == 403
    assert _post(d, d["other"], path, {}).status_code == 403
    assert _post(
        d, d["client_a"], f"/api/v1/dev/auto/claims/{claim}/redeem",
        {"proof": "bad" * 15},
    ).status_code == 200
    assert _reissue(d).status_code == 403
    with psycopg.connect(DSN) as db:
        assert db.execute(
            "SELECT count(*) FROM auto_vehicle_claim_reissue WHERE claim_id=%s",
            (claim,),
        ).fetchone()[0] == 0
    # Уже принятый proof тем более нельзя заменять.
    assert _post(
        d, d["client_a"], f"/api/v1/dev/auto/claims/{claim}/redeem",
        {"proof": original},
    ).status_code == 200
    assert _reissue(d).status_code == 403


def test_reissue_requires_current_original_issuer_scope(monkeypatch):
    _flags(monkeypatch)
    d = _scenario()
    claim, _ = _issue(d)
    with psycopg.connect(DSN) as db:
        db.execute(
            """UPDATE auto_vehicle_staff_assignment SET revoked_at=now()
               WHERE vehicle_id=%s AND user_id=%s AND role_code='INTAKE'""",
            (d["vehicle"], d["intake"]),
        )
    assert _reissue(d).status_code == 403
    with psycopg.connect(DSN) as db:
        assert db.execute(
            "SELECT count(*) FROM auto_vehicle_claim_reissue WHERE claim_id=%s",
            (claim,),
        ).fetchone()[0] == 0


def test_concurrent_reissue_yields_one_replacement_and_one_audit(monkeypatch):
    _flags(monkeypatch)
    d = _scenario()
    claim, _ = _issue(d)

    def attempt(_):
        return _reissue(d).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = sorted(pool.map(attempt, range(2)))
    assert results == [200, 403]
    with psycopg.connect(DSN) as db:
        assert db.execute(
            "SELECT count(*) FROM auto_vehicle_claim_reissue WHERE claim_id=%s",
            (claim,),
        ).fetchone()[0] == 1
        assert db.execute(
            """SELECT count(*) FROM auto_vehicle_claim_audit
               WHERE claim_id=%s AND action='REISSUED'""", (claim,),
        ).fetchone()[0] == 1


def test_reissue_consumes_global_issue_budget_and_429_preserves_old_code(monkeypatch):
    _flags(monkeypatch)
    d = _scenario()
    claim, old_proof = _issue(d)
    with psycopg.connect(DSN) as db:
        db.execute(
            """UPDATE auto_proof_throttle SET attempts=12
               WHERE actor_id=%s AND operation='ISSUE'""", (d["intake"],),
        )
    exhausted = _reissue(d)
    assert exhausted.status_code == 429
    assert exhausted.json()["code"] == "RATE_LIMITED"
    assert exhausted.headers["retry-after"] == "3600"
    assert "proof" not in exhausted.json()
    with psycopg.connect(DSN) as db:
        assert db.execute(
            "SELECT count(*) FROM auto_vehicle_claim_reissue WHERE claim_id=%s",
            (claim,),
        ).fetchone()[0] == 0
        assert db.execute(
            """SELECT count(*) FROM auto_vehicle_claim_audit
               WHERE claim_id=%s AND action='REISSUED'""", (claim,),
        ).fetchone()[0] == 0
        db.execute(
            """UPDATE auto_proof_throttle SET window_started_at=now()-interval '61 minutes'
               WHERE actor_id=%s AND operation='ISSUE'""", (d["intake"],),
        )
    result = _reissue(d)
    assert result.status_code == 200
    assert result.json()["proof"] != old_proof
