"""PG17 negative tests for closed internal organization staff administration."""
from uuid import uuid4

import psycopg
import pytest

from roboport_api.auto_org_staff import grant_staff, revoke_staff, OrgStaffDenied
from test_pg_auto_vehicle_claim_http import _scenario
from test_pg_baseline import DSN


def test_org_admin_is_required_and_actor_audit_survives_revocation():
    d = _scenario()
    org, foreign_org, location = uuid4(), uuid4(), uuid4()
    with psycopg.connect(DSN) as db:
        for ident in (org, foreign_org):
            db.execute(
                "INSERT INTO auto_organization(id,code,display_name) VALUES(%s,%s,'Synthetic')",
                (ident, "ORG_" + ident.hex[:10].upper()),
            )
        db.execute(
            "INSERT INTO auto_service_location(id,code,organization_id) VALUES(%s,%s,%s)",
            (location, "LOC_" + location.hex[:10].upper(), org),
        )
    # A legacy MANAGER is not an auto organization ADMIN.
    with psycopg.connect(DSN) as db:
        with pytest.raises(OrgStaffDenied):
            grant_staff(db, d["tokens"][d["manager"]], org,
                        d["intake"], "INTAKE", location)
    # Closed, synthetic DB-only bootstrap fixture; no public ADMIN issuance.
    with psycopg.connect(DSN) as db:
        db.execute(
            """INSERT INTO auto_organization_role_grant
               (id,organization_id,user_id,role_code) VALUES(%s,%s,%s,'ADMIN')""",
            (uuid4(), org, d["manager"]),
        )
    with psycopg.connect(DSN) as db:
        with pytest.raises(OrgStaffDenied):
            grant_staff(db, d["tokens"][d["manager"]], org,
                        d["manager"], "MANAGER", location)
        with pytest.raises(OrgStaffDenied):
            grant_staff(db, d["tokens"][d["manager"]], foreign_org,
                        d["intake"], "INTAKE", location)
        with pytest.raises(OrgStaffDenied):
            grant_staff(db, d["tokens"][d["manager"]], org,
                        d["intake"], "ADMIN", location)
        granted = grant_staff(db, d["tokens"][d["manager"]], org,
                              d["intake"], "INTAKE", location)
        assert revoke_staff(db, d["tokens"][d["manager"]], org, granted) == "REVOKED"
        assert revoke_staff(db, d["tokens"][d["manager"]], org, granted) == "ALREADY_REVOKED"
        assert db.execute(
            """SELECT actor_id,action FROM auto_org_staff_action
               WHERE grant_id=%s ORDER BY created_at,id""", (granted,),
        ).fetchall() == [(d["manager"], "GRANTED"), (d["manager"], "REVOKED")]
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("DELETE FROM auto_org_staff_action WHERE grant_id=%s", (granted,))


def test_revoke_and_authorization_are_serialized_at_pg17():
    """A completed revoke denies all later checks, while in-flight mutation holds locks."""
    from roboport_api.auto_org_authority import organization_role_allows

    d = _scenario()
    org, loc = uuid4(), uuid4()
    with psycopg.connect(DSN) as db:
        db.execute(
            "INSERT INTO auto_organization(id,code,display_name) VALUES(%s,%s,'Synthetic')",
            (org, "ORG_" + org.hex[:10].upper()),
        )
        db.execute(
            "INSERT INTO auto_service_location(id,code,organization_id) VALUES(%s,%s,%s)",
            (loc, "LOC_" + loc.hex[:10].upper(), org),
        )
        db.execute(
            """INSERT INTO auto_organization_role_grant
               (id,organization_id,user_id,role_code) VALUES(%s,%s,%s,'ADMIN')""",
            (uuid4(), org, d["manager"]),
        )
    with psycopg.connect(DSN) as db:
        grant = grant_staff(db, d["tokens"][d["manager"]], org,
                            d["intake"], "INTAKE", loc)

    with psycopg.connect(DSN) as checking:
        with checking.transaction():
            assert organization_role_allows(
                checking, d["tokens"][d["intake"]], org, "INTAKE", loc,
                for_mutation=True,
            )
            # A competing revoke cannot complete while this transaction
            # holds its FOR SHARE lock on the grant row.
            with psycopg.connect(DSN) as competitor:
                competitor.execute("SET lock_timeout = '200ms'")
                with pytest.raises(psycopg.errors.LockNotAvailable):
                    with competitor.transaction():
                        revoke_staff(competitor, d["tokens"][d["manager"]],
                                     org, grant)

    with psycopg.connect(DSN) as db:
        assert revoke_staff(db, d["tokens"][d["manager"]], org, grant) == "REVOKED"
    with psycopg.connect(DSN) as db:
        assert not organization_role_allows(
            db, d["tokens"][d["intake"]], org, "INTAKE", loc,
        )
        assert db.execute(
            "SELECT count(*) FROM auto_org_staff_action WHERE grant_id=%s AND action='REVOKED'",
            (grant,),
        ).fetchone()[0] == 1


def test_regrant_after_revocation_uses_fresh_identity_and_audit():
    d = _scenario()
    org = uuid4()
    with psycopg.connect(DSN) as db:
        db.execute(
            "INSERT INTO auto_organization(id,code,display_name) VALUES(%s,%s,'Synthetic')",
            (org, "ORG_" + org.hex[:10].upper()),
        )
        db.execute(
            """INSERT INTO auto_organization_role_grant
               (id,organization_id,user_id,role_code) VALUES(%s,%s,%s,'ADMIN')""",
            (uuid4(), org, d["manager"]),
        )
    with psycopg.connect(DSN) as db:
        first = grant_staff(db, d["tokens"][d["manager"]], org,
                            d["intake"], "WAREHOUSE")
        assert revoke_staff(db, d["tokens"][d["manager"]], org, first) == "REVOKED"
        second = grant_staff(db, d["tokens"][d["manager"]], org,
                             d["intake"], "WAREHOUSE")
        assert second != first
        assert db.execute(
            """SELECT id,revoked_at IS NULL FROM auto_organization_role_grant
               WHERE id IN (%s,%s) ORDER BY created_at,id""",
            (first, second),
        ).fetchall()  # Both original and re-grant must remain in immutable history.
        assert db.execute(
            "SELECT count(*) FROM auto_org_staff_action WHERE grant_id IN (%s,%s)",
            (first, second),
        ).fetchone()[0] == 3
