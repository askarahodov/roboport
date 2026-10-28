"""Live PG17 authorization is tenant-bound and reacts to revocation."""
from uuid import uuid4
import psycopg
from roboport_api.auto_org_authority import organization_role_allows
from test_pg_auto_vehicle_claim_http import _scenario
from test_pg_baseline import DSN


def test_organization_access_needs_scoped_grant_and_live_session():
    d = _scenario()
    org_a, org_b, loc_a, loc_b = uuid4(), uuid4(), uuid4(), uuid4()
    with psycopg.connect(DSN) as db:
        for oid in (org_a, org_b):
            db.execute("INSERT INTO auto_organization(id,code,display_name) VALUES(%s,%s,'Synthetic')",
                       (oid, "ORG_" + oid.hex[:10].upper()))
        for location, org in ((loc_a, org_a), (loc_b, org_b)):
            db.execute("INSERT INTO auto_service_location(id,code,organization_id) VALUES(%s,%s,%s)",
                       (location, "LOC_" + location.hex[:10].upper(), org))
        token = d["tokens"][d["manager"]]
        assert not organization_role_allows(db, token, org_a, "MANAGER", loc_a)
        grant = uuid4()
        db.execute("""INSERT INTO auto_organization_role_grant
            (id,organization_id,location_id,user_id,role_code)
            VALUES(%s,%s,%s,%s,'MANAGER')""", (grant, org_a, loc_a, d["manager"]))
        assert organization_role_allows(db, token, org_a, "MANAGER", loc_a)
        assert not organization_role_allows(db, token, org_b, "MANAGER", loc_b)
        assert not organization_role_allows(db, token, org_a, "INTAKE", loc_a)
        assert not organization_role_allows(db, "invalid", org_a, "MANAGER", loc_a)
        db.execute("UPDATE auto_organization_role_grant SET revoked_at=now() WHERE id=%s", (grant,))
        assert not organization_role_allows(db, token, org_a, "MANAGER", loc_a)
