"""PG17: revocation of live session/account cannot overtake auto mutations."""
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor
from queue import Queue
from time import monotonic, sleep

import psycopg
import pytest

from roboport_api.auto_vehicle_claim import (
    VehicleClaimRejected, issue_auto_proof, redeem_auto_proof,
    review_auto_claim,
)
from roboport_api.auto_intake_delegation import grant_intake
from roboport_api.auto_vehicle_link_intake import create_pending_vehicle_link
from roboport_api.session_lookup import session_subject
from test_pg_auto_vehicle_claim_http import _scenario
from test_pg_baseline import DSN


def _blocked(sql: str, args: tuple) -> None:
    """Second connection fails with lock timeout while first holds grant."""
    with psycopg.connect(DSN) as competitor:
        competitor.execute("SET LOCAL lock_timeout = '200ms'")
        with pytest.raises(psycopg.errors.LockNotAvailable):
            with competitor.transaction():
                competitor.execute(sql, args)


def _invalidate_session(actor) -> None:
    with psycopg.connect(DSN) as db:
        db.execute(
            "UPDATE user_session SET revoked_at=now() WHERE user_id=%s",
            (actor,),
        )


def test_issue_holds_staff_session_and_active_user_until_commit():
    d = _scenario()
    with psycopg.connect(DSN) as issuing:
        claim, proof = issue_auto_proof(
            issuing, d["tokens"][d["intake"]], d["link"],
        )
        assert len(proof) > 40
        _blocked(
            "UPDATE user_session SET revoked_at=now() WHERE user_id=%s",
            (d["intake"],),
        )
        _blocked(
            "UPDATE app_user SET status='DISABLED' WHERE id=%s",
            (d["intake"],),
        )
    _invalidate_session(d["intake"])
    with psycopg.connect(DSN) as denied:
        with pytest.raises(VehicleClaimRejected):
            issue_auto_proof(denied, d["tokens"][d["intake"]], d["other_link"])
    with psycopg.connect(DSN) as db:
        assert db.execute(
            "SELECT count(*) FROM auto_vehicle_claim WHERE link_id=%s",
            (d["other_link"],),
        ).fetchone() == (0,)
        assert db.execute(
            "SELECT count(*) FROM auto_vehicle_claim_audit WHERE claim_id=%s",
            (claim,),
        ).fetchone() == (1,)


def test_redeem_holds_client_session_and_status_and_rejects_later_disable():
    d = _scenario()
    with psycopg.connect(DSN) as db:
        claim, proof = issue_auto_proof(
            db, d["tokens"][d["intake"]], d["link"],
        )
    with psycopg.connect(DSN) as claiming:
        assert redeem_auto_proof(
            claiming, d["tokens"][d["client_a"]], claim, proof,
        ) == "PENDING_REVIEW"
        _blocked(
            "UPDATE user_session SET revoked_at=now() WHERE user_id=%s",
            (d["client_a"],),
        )
        _blocked(
            "UPDATE app_user SET status='DISABLED' WHERE id=%s",
            (d["client_a"],),
        )
    with psycopg.connect(DSN) as db:
        db.execute(
            "UPDATE app_user SET status='DISABLED' WHERE id=%s",
            (d["client_a"],),
        )
    with psycopg.connect(DSN) as db:
        with pytest.raises(VehicleClaimRejected):
            redeem_auto_proof(db, d["tokens"][d["client_a"]], claim, proof)
        with pytest.raises(VehicleClaimRejected):
            review_auto_claim(db, d["tokens"][d["manager"]], claim, True)
    with psycopg.connect(DSN) as db:
        assert db.execute(
            "SELECT verified_at FROM auto_vehicle_customer_link WHERE id=%s",
            (d["link"],),
        ).fetchone() == (None,)


def test_review_holds_manager_session_and_account_status_until_commit():
    d = _scenario()
    with psycopg.connect(DSN) as db:
        claim, proof = issue_auto_proof(
            db, d["tokens"][d["intake"]], d["link"],
        )
    with psycopg.connect(DSN) as db:
        assert redeem_auto_proof(
            db, d["tokens"][d["client_a"]], claim, proof,
        ) == "PENDING_REVIEW"
    key = uuid4().hex
    with psycopg.connect(DSN) as reviewing:
        assert review_auto_claim(
            reviewing, d["tokens"][d["manager"]], claim, True,
            idempotency_key=key,
        ) == "VERIFIED"
        _blocked(
            "UPDATE user_session SET revoked_at=now() WHERE user_id=%s",
            (d["manager"],),
        )
        _blocked(
            "UPDATE app_user SET status='DISABLED' WHERE id=%s",
            (d["manager"],),
        )
    _invalidate_session(d["manager"])
    with psycopg.connect(DSN) as db:
        with pytest.raises(VehicleClaimRejected):
            review_auto_claim(
                db, d["tokens"][d["manager"]], claim, True,
                idempotency_key=key,
            )
    with psycopg.connect(DSN) as db:
        assert db.execute(
            """SELECT count(*) FROM auto_vehicle_claim_audit
               WHERE claim_id=%s AND action='VERIFIED'""",
            (claim,),
        ).fetchone() == (1,)


def test_manager_delegation_holds_session_and_identity_before_grant():
    d = _scenario()
    with psycopg.connect(DSN) as db:
        db.execute(
            "INSERT INTO user_role(id,user_id,role_code) VALUES(%s,%s,'INTAKE')",
            (uuid4(), d["other"]),
        )
    with psycopg.connect(DSN) as delegating:
        # grant_intake creates a transaction itself. An outer transaction is
        # needed to observe locks *before* commit, not after the function ends.
        with delegating.transaction():
            granted = grant_intake(
                delegating, d["tokens"][d["manager"]], d["vehicle"], d["other"],
            )
            _blocked(
                "UPDATE user_session SET revoked_at=now() WHERE user_id=%s",
                (d["manager"],),
            )
            _blocked(
                "UPDATE app_user SET status='DISABLED' WHERE id=%s",
                (d["manager"],),
            )
    _invalidate_session(d["manager"])
    with psycopg.connect(DSN) as db:
        assert db.execute(
            "SELECT count(*) FROM auto_vehicle_staff_assignment_audit WHERE assignment_id=%s",
            (granted,),
        ).fetchone() == (1,)
        with pytest.raises(VehicleClaimRejected):
            grant_intake(
                db, d["tokens"][d["manager"]], d["vehicle"], d["other"],
            )


def test_pending_link_holds_target_client_eligibility_until_commit():
    d = _scenario()
    vehicle = uuid4()
    with psycopg.connect(DSN) as db:
        db.execute(
            "INSERT INTO auto_vehicle(id,make,model) VALUES(%s,'Synthetic','Eligibility')",
            (vehicle,),
        )
        db.execute(
            """INSERT INTO auto_vehicle_staff_assignment(id,vehicle_id,user_id,role_code)
               VALUES(%s,%s,%s,'INTAKE')""",
            (uuid4(), vehicle, d["intake"]),
        )
    with psycopg.connect(DSN) as proposing:
        # Keep outer transaction open: create_pending_vehicle_link owns its
        # own transaction when called without a surrounding scope.
        with proposing.transaction():
            link = create_pending_vehicle_link(
                proposing, d["tokens"][d["intake"]], vehicle, d["ca"],
            )
            _blocked(
                "UPDATE account_customer_link SET revoked_at=now() WHERE user_id=%s",
                (d["client_a"],),
            )
            _blocked(
                "UPDATE user_role SET revoked_at=now() WHERE user_id=%s AND role_code='CLIENT'",
                (d["client_a"],),
            )
            _blocked(
                "UPDATE app_user SET status='DISABLED' WHERE id=%s",
                (d["client_a"],),
            )
    with psycopg.connect(DSN) as db:
        db.execute(
            "UPDATE account_customer_link SET revoked_at=now() WHERE user_id=%s",
            (d["client_a"],),
        )
    another_vehicle = uuid4()
    with psycopg.connect(DSN) as db:
        db.execute(
            "INSERT INTO auto_vehicle(id,make,model) VALUES(%s,'Synthetic','Denied')",
            (another_vehicle,),
        )
        db.execute(
            """INSERT INTO auto_vehicle_staff_assignment(id,vehicle_id,user_id,role_code)
               VALUES(%s,%s,%s,'INTAKE')""",
            (uuid4(), another_vehicle, d["intake"]),
        )
    with psycopg.connect(DSN) as db:
        with pytest.raises(VehicleClaimRejected):
            create_pending_vehicle_link(
                db, d["tokens"][d["intake"]], another_vehicle, d["ca"],
            )
    with psycopg.connect(DSN) as db:
        assert db.execute(
            "SELECT count(*) FROM auto_vehicle_link_intake_audit WHERE link_id=%s",
            (link,),
        ).fetchone() == (1,)
        assert db.execute(
            "SELECT count(*) FROM auto_vehicle_customer_link WHERE vehicle_id=%s",
            (another_vehicle,),
        ).fetchone() == (0,)


def test_password_change_lock_order_never_deadlocks_with_mutation_session():
    """Concurrent password rotation locks user before sessions, as auto does.

    Wait until the worker is *actually waiting for the user row lock*, rather
    than relying on sleep timing. It must not hold a session row lock while
    waiting: otherwise change-password cannot revoke sessions and deadlocks.
    """
    d = _scenario()
    actor = d["manager"]
    started = Queue(maxsize=1)

    def mutate():
        with psycopg.connect(DSN) as worker:
            worker.execute("SET LOCAL lock_timeout='3s'")
            pid = worker.execute("SELECT pg_backend_pid()").fetchone()[0]
            started.put(pid)
            return session_subject(
                worker, d["tokens"][actor], for_mutation=True,
            )

    with ThreadPoolExecutor(max_workers=1) as pool:
        with psycopg.connect(DSN) as changing:
            changing_pid = changing.execute("SELECT pg_backend_pid()").fetchone()[0]
            changing.execute(
                "UPDATE app_user SET password_changed_at=now() WHERE id=%s",
                (actor,),
            )
            future = pool.submit(mutate)
            worker_pid = started.get(timeout=3)
            # Observe a real PostgreSQL lock wait to force the race: in the
            # old code, the worker could hold s before waiting for u.
            end = monotonic() + 2.0
            blocked = False
            with psycopg.connect(DSN) as observer:
                while monotonic() < end:
                    blockers = observer.execute(
                        "SELECT pg_blocking_pids(%s)", (worker_pid,),
                    ).fetchone()[0]
                    if changing_pid in blockers:
                        blocked = True
                        break
                    sleep(0.01)
            assert blocked, "worker did not reach the conflicting user lock"
            # Same order as real change_password: update user, then sessions.
            # If worker had locked session first, PG would deadlock here.
            changing.execute(
                "UPDATE user_session SET revoked_at=now() WHERE user_id=%s",
                (actor,),
            )
        # After the password-change transaction commits, locked session
        # lookup has to re-check revocation and reject the old cookie.
        assert future.result(timeout=4) is None
