"""PG17 retention: stale throttle rows only; live budgets/audit untouched."""
from uuid import uuid4

import psycopg
import pytest

from roboport_api.auto_throttle_maintenance import (
    MAX_BATCH, expired_counts, prune_expired,
)
from test_pg_auto_vehicle_claim_http import _scenario
from test_pg_baseline import DSN


def _counter(conn, actor, operation, age, attempts=3):
    conn.execute(
        """INSERT INTO auto_proof_throttle(
             actor_id,operation,attempts,window_started_at
           ) VALUES(%s,%s,%s,now() - %s::interval)""",
        (actor, operation, attempts, age),
    )


def test_cleanup_is_bounded_and_preserves_current_and_recent_counters():
    d = _scenario()
    with psycopg.connect(DSN) as conn:
        _counter(conn, d["intake"], "ISSUE", "48 hours")
        _counter(conn, d["client_a"], "REDEEM", "25 hours")
        _counter(conn, d["client_b"], "REDEEM", "16 minutes", 8)
        _counter(conn, d["manager"], "ISSUE", "61 minutes", 12)

    with psycopg.connect(DSN) as conn:
        assert expired_counts(conn) == {"ISSUE": 1, "REDEEM": 1}
        assert prune_expired(conn, batch_size=1) in (
            {"ISSUE": 1}, {"REDEEM": 1},
        )
    with psycopg.connect(DSN) as conn:
        assert sum(expired_counts(conn).values()) == 1
        assert sum(prune_expired(conn, batch_size=1).values()) == 1
        assert expired_counts(conn) == {}
        assert prune_expired(conn, batch_size=1) == {}
        assert conn.execute(
            """SELECT attempts FROM auto_proof_throttle
               WHERE actor_id=%s AND operation='REDEEM'""",
            (d["client_b"],),
        ).fetchone() == (8,)
        assert conn.execute(
            """SELECT attempts FROM auto_proof_throttle
               WHERE actor_id=%s AND operation='ISSUE'""",
            (d["manager"],),
        ).fetchone() == (12,)
        assert conn.execute(
            """SELECT count(*) FROM auto_proof_throttle
               WHERE actor_id IN (%s,%s)""",
            (d["intake"], d["client_a"]),
        ).fetchone() == (0,)


def test_cleanup_skips_locked_row_and_never_resets_active_limit():
    d = _scenario()
    with psycopg.connect(DSN) as conn:
        _counter(conn, d["client_a"], "REDEEM", "30 hours", 9)

    with psycopg.connect(DSN) as live:
        live.execute(
            """SELECT attempts FROM auto_proof_throttle
               WHERE actor_id=%s AND operation='REDEEM' FOR UPDATE""",
            (d["client_a"],),
        )
        with psycopg.connect(DSN) as maintenance:
            maintenance.execute("SET LOCAL lock_timeout='200ms'")
            assert prune_expired(maintenance) == {}
        # The stale row is still present while another operation holds it.
        assert live.execute(
            """SELECT attempts FROM auto_proof_throttle
               WHERE actor_id=%s AND operation='REDEEM'""",
            (d["client_a"],),
        ).fetchone() == (9,)

    with psycopg.connect(DSN) as conn:
        assert prune_expired(conn) == {"REDEEM": 1}


@pytest.mark.parametrize("batch_size", [0, -1, 1001, True, 1.5, "10", None])
def test_invalid_batch_size_cannot_modify_db(batch_size):
    d = _scenario()
    with psycopg.connect(DSN) as conn:
        _counter(conn, d["client_a"], "REDEEM", "30 hours")
    with psycopg.connect(DSN) as conn:
        # Это общий PG17 CI: параметризованные случаи не должны полагаться
        # на отсутствие чужих просроченных счётчиков.
        before = expired_counts(conn)
        with pytest.raises(ValueError):
            prune_expired(conn, batch_size=batch_size)
        assert expired_counts(conn) == before
        assert conn.execute(
            """SELECT attempts FROM auto_proof_throttle
               WHERE actor_id=%s AND operation='REDEEM'""",
            (d["client_a"],),
        ).fetchone() == (3,)
        # Удаляем только собственную synthetic fixture, не чужие записи.
        conn.execute(
            """DELETE FROM auto_proof_throttle
               WHERE actor_id=%s AND operation='REDEEM'""",
            (d["client_a"],),
        )


def test_cleanup_does_not_delete_proof_audit_or_retry_receipts():
    from roboport_api.auto_vehicle_claim import issue_auto_proof, redeem_auto_proof
    d = _scenario()
    with psycopg.connect(DSN) as conn:
        claim, proof = issue_auto_proof(
            conn, d["tokens"][d["intake"]], d["link"],
        )
    with psycopg.connect(DSN) as conn:
        assert redeem_auto_proof(
            conn, d["tokens"][d["client_a"]], claim, proof,
            idempotency_key=uuid4().hex,
        ) == "PENDING_REVIEW"
    with psycopg.connect(DSN) as conn:
        before = (
            conn.execute(
                "SELECT count(*) FROM auto_vehicle_claim_audit WHERE claim_id=%s",
                (claim,),
            ).fetchone()[0],
            conn.execute(
                "SELECT count(*) FROM auto_claim_retry_receipt WHERE claim_id=%s",
                (claim,),
            ).fetchone()[0],
        )
        # Stale counters for unrelated actors; never touch the active ISSUE/REDEEM.
        _counter(conn, d["client_b"], "ISSUE", "30 hours")
        assert prune_expired(conn) == {"ISSUE": 1}
        after = (
            conn.execute(
                "SELECT count(*) FROM auto_vehicle_claim_audit WHERE claim_id=%s",
                (claim,),
            ).fetchone()[0],
            conn.execute(
                "SELECT count(*) FROM auto_claim_retry_receipt WHERE claim_id=%s",
                (claim,),
            ).fetchone()[0],
        )
        assert before == after == (2, 1)
        assert conn.execute(
            "SELECT count(*) FROM auto_proof_throttle WHERE actor_id IN (%s,%s)",
            (d["intake"], d["client_a"]),
        ).fetchone() == (2,)

assert MAX_BATCH == 1000
