"""PG17: organizations cannot silently inherit legacy locations/roles."""
from uuid import uuid4

import psycopg
import pytest

from test_pg_baseline import DSN


def test_organization_assignment_is_explicit_and_immutable():
    a, b, location = uuid4(), uuid4(), uuid4()
    with psycopg.connect(DSN) as db:
        for org, code in ((a, "ORG_" + a.hex[:8].upper()),
                          (b, "ORG_" + b.hex[:8].upper())):
            db.execute("""INSERT INTO auto_organization
                (id,code,display_name) VALUES (%s,%s,'Synthetic')""",
                (org, code))
        db.execute("""INSERT INTO auto_service_location(id,code)
            VALUES(%s,%s)""", (location, "LOC_" + location.hex[:8].upper()))
        # Never infer tenant from a previous synthetic role or location.
        assert db.execute("""SELECT organization_id
            FROM auto_service_location WHERE id=%s""",
            (location,)).fetchone() == (None,)
        db.execute("""UPDATE auto_service_location
            SET organization_id=%s WHERE id=%s""", (a, location))
        assert db.execute("""SELECT organization_id
            FROM auto_service_location WHERE id=%s""",
            (location,)).fetchone() == (a,)
        for attempted in (b, None):
            with pytest.raises(psycopg.errors.RaiseException):
                with db.transaction():
                    db.execute("""UPDATE auto_service_location
                        SET organization_id=%s WHERE id=%s""",
                        (attempted, location))
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            with db.transaction():
                db.execute("""INSERT INTO auto_service_location
                    (id,code,organization_id) VALUES(%s,%s,%s)""",
                    (uuid4(), "FOREIGN_" + uuid4().hex[:8].upper(), uuid4()))


def test_organization_identity_and_revocation_cannot_be_rewritten():
    org = uuid4()
    with psycopg.connect(DSN) as db:
        db.execute("""INSERT INTO auto_organization(id,code,display_name)
            VALUES(%s,%s,'Synthetic')""",
            (org, "ORG_" + org.hex[:8].upper()))
        db.execute("UPDATE auto_organization SET disabled_at=now() WHERE id=%s", (org,))
        for sql in (
            "UPDATE auto_organization SET disabled_at=NULL WHERE id=%s",
            "UPDATE auto_organization SET display_name='changed' WHERE id=%s",
            "DELETE FROM auto_organization WHERE id=%s",
        ):
            with pytest.raises(psycopg.errors.RaiseException):
                with db.transaction():
                    db.execute(sql, (org,))
