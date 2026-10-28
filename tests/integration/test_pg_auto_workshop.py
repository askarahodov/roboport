"""PG17 organization/location isolation for workshop assets."""
from uuid import uuid4

import psycopg
import pytest
from test_pg_auto_vehicle_claim_http import _scenario
from test_pg_baseline import DSN


def test_work_bay_and_staff_shift_refuse_cross_tenant_and_mutation():
    d = _scenario()
    org_a, org_b, loc_a, bay, shift = (uuid4() for _ in range(5))
    with psycopg.connect(DSN) as db:
        for oid in (org_a, org_b):
            db.execute("INSERT INTO auto_organization(id,code,display_name) VALUES(%s,%s,'Synthetic')",
                       (oid, "ORG_" + oid.hex[:10].upper()))
        db.execute("INSERT INTO auto_service_location(id,code,organization_id) VALUES(%s,%s,%s)",
                   (loc_a, "LOC_" + loc_a.hex[:10].upper(), org_a))
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("""INSERT INTO auto_work_bay(id,organization_id,location_id,code)
                              VALUES(%s,%s,%s,'BAY_1')""", (uuid4(), org_b, loc_a))
        db.execute("""INSERT INTO auto_work_bay(id,organization_id,location_id,code)
                      VALUES(%s,%s,%s,'BAY_1')""", (bay, org_a, loc_a))
        with pytest.raises(psycopg.errors.UniqueViolation):
            with db.transaction():
                db.execute("""INSERT INTO auto_work_bay(id,organization_id,location_id,code)
                              VALUES(%s,%s,%s,'BAY_1')""", (uuid4(), org_a, loc_a))
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("UPDATE auto_work_bay SET organization_id=%s WHERE id=%s", (org_b, bay))
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("""INSERT INTO auto_staff_shift
                  (id,organization_id,location_id,user_id,starts_at,ends_at)
                  VALUES(%s,%s,%s,%s,now()+interval '1 day',now()+interval '1 day 8 hours')""",
                  (uuid4(), org_b, loc_a, d["intake"]))
        db.execute("""INSERT INTO auto_staff_shift
          (id,organization_id,location_id,user_id,starts_at,ends_at)
          VALUES(%s,%s,%s,%s,now()+interval '1 day',now()+interval '1 day 8 hours')""",
          (shift, org_a, loc_a, d["intake"]))
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("UPDATE auto_staff_shift SET user_id=%s WHERE id=%s", (d["manager"], shift))
        db.execute("UPDATE auto_staff_shift SET canceled_at=now() WHERE id=%s", (shift,))
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("UPDATE auto_staff_shift SET canceled_at=NULL WHERE id=%s", (shift,))
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("DELETE FROM auto_work_bay WHERE id=%s", (bay,))
