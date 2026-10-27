"""PG17: optional service-location scope is deny-by-default when enabled.

All data are synthetic. Branch grants are inserted by test fixtures, NOT by a
public API or unverified manager bootstrap.
"""
from uuid import uuid4

import psycopg
import pytest

from roboport_api.auto_vehicle_claim import (
    VehicleClaimRejected, issue_auto_proof, redeem_auto_proof, review_auto_claim,
)
from roboport_api.auto_intake_delegation import grant_intake
from test_pg_auto_vehicle_claim_http import _scenario
from test_pg_baseline import DSN


def _pilot(monkeypatch):
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTO_BRANCH_SCOPE", "1")


def _seed(d, *, bind=True, intake=True, manager=True):
    location, wrong = uuid4(), uuid4()
    grants = {}
    with psycopg.connect(DSN) as db:
        for ident in (location, wrong):
            db.execute(
                "INSERT INTO auto_service_location(id,code) VALUES(%s,%s)",
                (ident, "SYN_" + ident.hex[:10].upper()),
            )
        if bind:
            db.execute(
                """INSERT INTO auto_vehicle_location_binding(vehicle_id,location_id)
                   VALUES(%s,%s)""",
                (d["vehicle"], location),
            )
        for label, enabled in (("INTAKE", intake), ("MANAGER", manager)):
            if enabled:
                actor = d["intake"] if label == "INTAKE" else d["manager"]
                ident = uuid4()
                db.execute(
                    """INSERT INTO auto_location_role_grant
                       (id,location_id,user_id,role_code)
                       VALUES(%s,%s,%s,%s)""",
                    (ident, location, actor, label),
                )
                grants[label] = ident
    return location, wrong, grants


def _issue(d):
    with psycopg.connect(DSN) as db:
        return issue_auto_proof(db, d["tokens"][d["intake"]], d["link"])


def _redeem(d, claim, proof):
    with psycopg.connect(DSN) as db:
        return redeem_auto_proof(db, d["tokens"][d["client_a"]], claim, proof)


def test_pilot_requires_location_binding_and_explicit_both_staff_roles(monkeypatch):
    _pilot(monkeypatch)
    d = _scenario()
    # Existing vehicle assignment and global role do not imply branch scope.
    with psycopg.connect(DSN) as db:
        with pytest.raises(VehicleClaimRejected):
            issue_auto_proof(db, d["tokens"][d["intake"]], d["link"])
    loc, _, grants = _seed(d, bind=False, manager=False)
    with psycopg.connect(DSN) as db:
        with pytest.raises(VehicleClaimRejected):
            issue_auto_proof(db, d["tokens"][d["intake"]], d["link"])
        db.execute(
            """INSERT INTO auto_vehicle_location_binding(vehicle_id,location_id)
               VALUES(%s,%s)""",
            (d["vehicle"], loc),
        )
    claim, proof = _issue(d)
    assert _redeem(d, claim, proof) == "PENDING_REVIEW"
    with psycopg.connect(DSN) as db:
        with pytest.raises(VehicleClaimRejected):
            review_auto_claim(db, d["tokens"][d["manager"]], claim, True)
        assert db.execute(
            "SELECT verified_at FROM auto_vehicle_customer_link WHERE id=%s",
            (d["link"],),
        ).fetchone() == (None,)
        db.execute(
            """INSERT INTO auto_location_role_grant(id,location_id,user_id,role_code)
               VALUES(%s,%s,%s,'MANAGER')""",
            (uuid4(), loc, d["manager"]),
        )
    with psycopg.connect(DSN) as db:
        assert review_auto_claim(db, d["tokens"][d["manager"]], claim, True) == "VERIFIED"
    with psycopg.connect(DSN) as db:
        assert db.execute(
            "SELECT count(*) FROM auto_vehicle_claim_audit WHERE claim_id=%s AND action='VERIFIED'",
            (claim,),
        ).fetchone() == (1,)


def test_cross_branch_grant_cannot_bypass_vehicle_assignment(monkeypatch):
    _pilot(monkeypatch)
    d = _scenario()
    loc, wrong, grants = _seed(d, intake=False)
    with psycopg.connect(DSN) as db:
        db.execute(
            """INSERT INTO auto_location_role_grant(id,location_id,user_id,role_code)
               VALUES(%s,%s,%s,'INTAKE')""",
            (uuid4(), wrong, d["intake"]),
        )
    with psycopg.connect(DSN) as db:
        with pytest.raises(VehicleClaimRejected):
            issue_auto_proof(db, d["tokens"][d["intake"]], d["link"])
    # Add the matching grant for this vehicle/branch.
    with psycopg.connect(DSN) as db:
        db.execute(
            """INSERT INTO auto_location_role_grant(id,location_id,user_id,role_code)
               VALUES(%s,%s,%s,'INTAKE')""",
            (uuid4(), loc, d["intake"]),
        )
    claim, proof = _issue(d)
    assert claim is not None and len(proof) > 40

    # A grant to a branch never automatically creates an object assignment.
    other_vehicle, other_link = uuid4(), uuid4()
    with psycopg.connect(DSN) as db:
        db.execute(
            "INSERT INTO auto_vehicle(id,make,model) VALUES(%s,'Synthetic','Elsewhere')",
            (other_vehicle,),
        )
        db.execute(
            """INSERT INTO auto_vehicle_customer_link(id,vehicle_id,customer_id)
               VALUES(%s,%s,%s)""",
            (other_link, other_vehicle, d["ca"]),
        )
        db.execute(
            """INSERT INTO auto_vehicle_location_binding(vehicle_id,location_id)
               VALUES(%s,%s)""",
            (other_vehicle, loc),
        )
    with psycopg.connect(DSN) as db:
        with pytest.raises(VehicleClaimRejected):
            issue_auto_proof(db, d["tokens"][d["intake"]], other_link)


def test_revoked_branch_grant_and_disabled_location_deny_future_mutations(monkeypatch):
    _pilot(monkeypatch)
    d = _scenario()
    loc, _, grants = _seed(d)
    with psycopg.connect(DSN) as db:
        db.execute(
            "UPDATE auto_location_role_grant SET revoked_at=now() WHERE id=%s",
            (grants["INTAKE"],),
        )
    with psycopg.connect(DSN) as db:
        with pytest.raises(VehicleClaimRejected):
            issue_auto_proof(db, d["tokens"][d["intake"]], d["link"])
        # Re-grant requires a new row; revoked row cannot be reactivated.
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute(
                    "UPDATE auto_location_role_grant SET revoked_at=NULL WHERE id=%s",
                    (grants["INTAKE"],),
                )
        db.execute(
            """INSERT INTO auto_location_role_grant(id,location_id,user_id,role_code)
               VALUES(%s,%s,%s,'INTAKE')""",
            (uuid4(), loc, d["intake"]),
        )
    claim, proof = _issue(d)
    assert _redeem(d, claim, proof) == "PENDING_REVIEW"
    with psycopg.connect(DSN) as db:
        db.execute(
            "UPDATE auto_service_location SET disabled_at=now() WHERE id=%s",
            (loc,),
        )
    with psycopg.connect(DSN) as db:
        with pytest.raises(VehicleClaimRejected):
            review_auto_claim(db, d["tokens"][d["manager"]], claim, True)
        assert db.execute(
            "SELECT verified_at FROM auto_vehicle_customer_link WHERE id=%s",
            (d["link"],),
        ).fetchone() == (None,)


def test_scope_row_locks_serialize_concurrent_grant_revocation(monkeypatch):
    _pilot(monkeypatch)
    d = _scenario()
    loc, _, grants = _seed(d)
    with psycopg.connect(DSN) as issuing:
        claim, proof = issue_auto_proof(
            issuing, d["tokens"][d["intake"]], d["link"],
        )
        assert claim is not None and len(proof) > 40
        with psycopg.connect(DSN) as competitor:
            competitor.execute("SET LOCAL lock_timeout = '200ms'")
            with pytest.raises(psycopg.errors.LockNotAvailable):
                with competitor.transaction():
                    competitor.execute(
                        "UPDATE auto_location_role_grant SET revoked_at=now() WHERE id=%s",
                        (grants["INTAKE"],),
                    )
            with pytest.raises(psycopg.errors.LockNotAvailable):
                with competitor.transaction():
                    competitor.execute(
                        "UPDATE auto_service_location SET disabled_at=now() WHERE id=%s",
                        (loc,),
                    )
    with psycopg.connect(DSN) as db:
        db.execute(
            "UPDATE auto_location_role_grant SET revoked_at=now() WHERE id=%s",
            (grants["INTAKE"],),
        )
    with psycopg.connect(DSN) as db:
        with pytest.raises(VehicleClaimRejected):
            issue_auto_proof(db, d["tokens"][d["intake"]], d["other_link"])


def test_branch_manager_scope_controls_delegation_not_role_bootstrap(monkeypatch):
    _pilot(monkeypatch)
    d = _scenario()
    _, _, grants = _seed(d, manager=False)
    with psycopg.connect(DSN) as db:
        db.execute(
            "INSERT INTO user_role(id,user_id,role_code) VALUES(%s,%s,'INTAKE')",
            (uuid4(), d["other"]),
        )
    with psycopg.connect(DSN) as db:
        with pytest.raises(VehicleClaimRejected):
            grant_intake(db, d["tokens"][d["manager"]], d["vehicle"], d["other"])
        assert db.execute(
            "SELECT count(*) FROM auto_vehicle_staff_assignment_audit WHERE target_user_id=%s",
            (d["other"],),
        ).fetchone() == (0,)
    # A location grant alone does not create MANAGER global role or vehicle assignment.
    loc = _seed_extra_grant_for_manager(d)
    with psycopg.connect(DSN) as db:
        with pytest.raises(VehicleClaimRejected):
            grant_intake(db, d["tokens"][d["other"]], d["vehicle"], d["intake"])
    with psycopg.connect(DSN) as db:
        assert grant_intake(db, d["tokens"][d["manager"]], d["vehicle"], d["other"]) is not None


def _seed_extra_grant_for_manager(d):
    with psycopg.connect(DSN) as db:
        loc = db.execute(
            "SELECT location_id FROM auto_vehicle_location_binding WHERE vehicle_id=%s",
            (d["vehicle"],),
        ).fetchone()[0]
        db.execute(
            """INSERT INTO auto_location_role_grant(id,location_id,user_id,role_code)
               VALUES(%s,%s,%s,'MANAGER')""",
            (uuid4(), loc, d["manager"]),
        )
    return loc


def test_guarded_mapping_and_grants_reject_rewrite_delete_and_duplicate(monkeypatch):
    _pilot(monkeypatch)
    d = _scenario()
    loc, other, grants = _seed(d)
    with psycopg.connect(DSN) as db:
        for statement, params in (
            ("UPDATE auto_vehicle_location_binding SET location_id=%s WHERE vehicle_id=%s",
             (other, d["vehicle"])),
            ("DELETE FROM auto_vehicle_location_binding WHERE vehicle_id=%s",
             (d["vehicle"],)),
            ("UPDATE auto_location_role_grant SET role_code='MANAGER' WHERE id=%s",
             (grants["INTAKE"],)),
            ("DELETE FROM auto_location_role_grant WHERE id=%s",
             (grants["MANAGER"],)),
            ("DELETE FROM auto_service_location WHERE id=%s", (loc,)),
        ):
            with pytest.raises(psycopg.errors.RaiseException):
                with db.transaction():
                    db.execute(statement, params)
        with pytest.raises(psycopg.errors.UniqueViolation):
            with db.transaction():
                db.execute(
                    """INSERT INTO auto_location_role_grant(id,location_id,user_id,role_code)
                       VALUES(%s,%s,%s,'MANAGER')""",
                    (uuid4(), loc, d["manager"]),
                )


def test_invalid_gate_value_denies_instead_of_silently_dropping_branch_scope(monkeypatch):
    d = _scenario()
    _seed(d)
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTO_BRANCH_SCOPE", "yes")
    with psycopg.connect(DSN) as db:
        with pytest.raises(VehicleClaimRejected):
            issue_auto_proof(db, d["tokens"][d["intake"]], d["link"])
