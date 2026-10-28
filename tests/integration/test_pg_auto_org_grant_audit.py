"""PostgreSQL audit for synthetic org grant lifecycle."""
from uuid import uuid4

import psycopg
import pytest

from test_pg_auto_vehicle_claim_http import _scenario
from test_pg_baseline import DSN


def test_org_grant_audit_is_append_only_and_revoke_is_once():
    d = _scenario()
    org, grant = uuid4(), uuid4()
    with psycopg.connect(DSN) as db:
        db.execute("""INSERT INTO auto_organization(id,code,display_name)
                      VALUES(%s,%s,'Synthetic')""",
                   (org, "ORG_" + org.hex[:10].upper()))
        db.execute("""INSERT INTO auto_organization_role_grant
                      (id,organization_id,user_id,role_code)
                      VALUES(%s,%s,%s,'MANAGER')""",
                   (grant, org, d["manager"]))
        assert db.execute("""SELECT action FROM auto_organization_grant_audit
                             WHERE grant_id=%s ORDER BY id""",
                          (grant,)).fetchall() == [("GRANTED",)]
        db.execute("""UPDATE auto_organization_role_grant
                      SET revoked_at=now() WHERE id=%s""", (grant,))
        # A no-op update must not generate a second REVOKED.
        db.execute("""UPDATE auto_organization_role_grant
                      SET revoked_at=revoked_at WHERE id=%s""", (grant,))
        assert db.execute("""SELECT action FROM auto_organization_grant_audit
                             WHERE grant_id=%s ORDER BY id""",
                          (grant,)).fetchall() == [("GRANTED",), ("REVOKED",)]
        for sql in (
            "DELETE FROM auto_organization_grant_audit WHERE grant_id=%s",
            "UPDATE auto_organization_grant_audit SET action='GRANTED' WHERE grant_id=%s",
        ):
            with pytest.raises(psycopg.errors.RaiseException):
                with db.transaction():
                    db.execute(sql, (grant,))
