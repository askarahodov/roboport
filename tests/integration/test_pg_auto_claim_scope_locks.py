"""PostgreSQL17: authorizations survive until commit of synthetic proof/review.

The competing statement uses a bounded lock_timeout: it must wait for the live
grant used by the request, rather than revoking it mid-mutation.
"""
import psycopg
import pytest

from roboport_api.auto_vehicle_claim import (
    VehicleClaimRejected, issue_auto_proof, redeem_auto_proof,
    review_auto_claim,
)
from test_pg_auto_vehicle_claim_http import _scenario
from test_pg_baseline import DSN


def _cannot_revoke_while_authorized(sql, params):
    """An independent transaction must not change the just-checked grant."""
    with psycopg.connect(DSN) as contender:
        contender.execute("SET LOCAL lock_timeout = '200ms'")
        with pytest.raises(psycopg.errors.LockNotAvailable):
            with contender.transaction():
                contender.execute(sql, params)


def _issue(data):
    with psycopg.connect(DSN) as db:
        return issue_auto_proof(db, data["tokens"][data["intake"]], data["link"])


def test_issue_holds_intake_role_and_vehicle_assignment_until_commit():
    data = _scenario()
    with psycopg.connect(DSN) as issuing:
        claim, proof = issue_auto_proof(
            issuing, data["tokens"][data["intake"]], data["link"],
        )
        assert len(proof) >= 40
        _cannot_revoke_while_authorized(
            """UPDATE user_role SET revoked_at=now()
               WHERE user_id=%s AND role_code='INTAKE'""",
            (data["intake"],),
        )
        _cannot_revoke_while_authorized(
            """UPDATE auto_vehicle_staff_assignment SET revoked_at=now()
               WHERE vehicle_id=%s AND user_id=%s AND role_code='INTAKE'""",
            (data["vehicle"], data["intake"]),
        )

    with psycopg.connect(DSN) as db:
        assert db.execute(
            "SELECT count(*) FROM auto_vehicle_claim WHERE id=%s", (claim,),
        ).fetchone()[0] == 1
        db.execute(
            """UPDATE auto_vehicle_staff_assignment SET revoked_at=now()
               WHERE vehicle_id=%s AND user_id=%s AND role_code='INTAKE'""",
            (data["vehicle"], data["intake"]),
        )
    with psycopg.connect(DSN) as db:
        with pytest.raises(VehicleClaimRejected):
            issue_auto_proof(db, data["tokens"][data["intake"]], data["other_link"])


def test_redeem_holds_client_role_and_customer_link_until_commit():
    data = _scenario()
    claim, proof = _issue(data)
    with psycopg.connect(DSN) as claiming:
        assert redeem_auto_proof(
            claiming, data["tokens"][data["client_a"]], claim, proof,
        ) == "PENDING_REVIEW"
        _cannot_revoke_while_authorized(
            """UPDATE account_customer_link SET revoked_at=now()
               WHERE user_id=%s AND customer_id=%s""",
            (data["client_a"], data["ca"]),
        )
        _cannot_revoke_while_authorized(
            """UPDATE user_role SET revoked_at=now()
               WHERE user_id=%s AND role_code='CLIENT'""",
            (data["client_a"],),
        )

    with psycopg.connect(DSN) as db:
        db.execute(
            "UPDATE account_customer_link SET revoked_at=now() WHERE user_id=%s",
            (data["client_a"],),
        )
    with psycopg.connect(DSN) as db:
        with pytest.raises(VehicleClaimRejected):
            review_auto_claim(db, data["tokens"][data["manager"]], claim, True)
        assert db.execute(
            "SELECT verified_at FROM auto_vehicle_customer_link WHERE id=%s",
            (data["link"],),
        ).fetchone()[0] is None


def test_review_holds_manager_scope_and_claimant_entitlement_until_commit():
    data = _scenario()
    claim, proof = _issue(data)
    with psycopg.connect(DSN) as db:
        assert redeem_auto_proof(
            db, data["tokens"][data["client_a"]], claim, proof,
        ) == "PENDING_REVIEW"

    with psycopg.connect(DSN) as reviewing:
        assert review_auto_claim(
            reviewing, data["tokens"][data["manager"]], claim, True,
        ) == "VERIFIED"
        _cannot_revoke_while_authorized(
            """UPDATE user_role SET revoked_at=now()
               WHERE user_id=%s AND role_code='MANAGER'""",
            (data["manager"],),
        )
        _cannot_revoke_while_authorized(
            """UPDATE auto_vehicle_staff_assignment SET revoked_at=now()
               WHERE vehicle_id=%s AND user_id=%s AND role_code='MANAGER'""",
            (data["vehicle"], data["manager"]),
        )
        _cannot_revoke_while_authorized(
            """UPDATE account_customer_link SET revoked_at=now()
               WHERE user_id=%s AND customer_id=%s""",
            (data["client_a"], data["ca"]),
        )

    with psycopg.connect(DSN) as db:
        assert db.execute(
            "SELECT verified_by FROM auto_vehicle_customer_link WHERE id=%s",
            (data["link"],),
        ).fetchone()[0] == data["manager"]
        db.execute(
            "UPDATE user_role SET revoked_at=now() WHERE user_id=%s AND role_code='MANAGER'",
            (data["manager"],),
        )
    with psycopg.connect(DSN) as db:
        with pytest.raises(VehicleClaimRejected):
            review_auto_claim(db, data["tokens"][data["manager"]], claim, True)
