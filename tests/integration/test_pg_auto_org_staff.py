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
