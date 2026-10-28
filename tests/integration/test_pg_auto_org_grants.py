"""PG17 tests for synthetic tenant-scoped role grants."""
from uuid import uuid4

import psycopg
import pytest

from test_pg_baseline import DSN


def test_location_grants_must_belong_to_the_same_organization():
    org_a, org_b, location = uuid4(), uuid4(), uuid4()
    with psycopg.connect(DSN) as db:
        for org in (org_a, org_b):
            db.execute("INSERT INTO auto_organization(id,code,display_name) VALUES(%s,%s,'Synthetic')",
                       (org, "ORG_" + org.hex[:10].upper()))
        db.execute("INSERT INTO auto_service_location(id,code,organization_id) VALUES(%s,%s,%s)",
                   (location, "LOC_" + location.hex[:10].upper(), org_a))
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("""INSERT INTO auto_organization_role_grant
                    (id,organization_id,location_id,user_id,role_code)
                    VALUES (%s,%s,%s,%s,'INTAKE')""",
                    (uuid4(), org_b, location, uuid4()))


def test_unassigned_location_cannot_be_used_as_scoped_grant():
    org, location = uuid4(), uuid4()
    with psycopg.connect(DSN) as db:
        db.execute("INSERT INTO auto_organization(id,code,display_name) VALUES(%s,%s,'Synthetic')",
                   (org, "ORG_" + org.hex[:10].upper()))
        db.execute("INSERT INTO auto_service_location(id,code) VALUES(%s,%s)",
                   (location, "LOC_" + location.hex[:10].upper()))
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("""INSERT INTO auto_organization_role_grant
                    (id,organization_id,location_id,user_id,role_code)
                    VALUES (%s,%s,%s,%s,'MANAGER')""",
                    (uuid4(), org, location, uuid4()))
