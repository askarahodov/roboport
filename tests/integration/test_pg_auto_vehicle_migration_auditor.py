"""Synthetic PG17 audit-role privilege boundaries for vehicle migration."""
from uuid import uuid4

import psycopg
import pytest
from test_pg_baseline import DSN
from test_pg_auto_bay_booking import _org_and_bay


ROLE = "roboport_vehicle_migration_auditor"


def test_migration_auditor_is_nologin_noinherit_without_sensitive_table_access():
    with psycopg.connect(DSN) as db:
        row = db.execute("""
            SELECT rolcanlogin, rolsuper, rolcreatedb, rolcreaterole, rolinherit
            FROM pg_roles WHERE rolname=%s
        """, (ROLE,)).fetchone()
        assert row == (False, False, False, False, False)
        with db.transaction():
            db.execute("SET LOCAL ROLE roboport_vehicle_migration_auditor")
            assert db.execute("SELECT current_user").fetchone()[0] == ROLE
            assert db.execute("""
                SELECT has_column_privilege(current_user,
                       'auto_vehicle_migration_batch','id','SELECT'),
                       has_column_privilege(current_user,
                       'auto_vehicle_migration_batch','row_count','SELECT')
            """).fetchone() == (True, True)
            for table in ("auto_vehicle_migration_line",
                          "auto_vehicle_migration_promotion", "auto_vehicle",
                          "auto_vehicle_customer_link"):
                assert db.execute("SELECT has_table_privilege(current_user,%s,'SELECT')",
                                  (table,)).fetchone()[0] is False
                assert db.execute("SELECT has_table_privilege(current_user,%s,'INSERT')",
                                  (table,)).fetchone()[0] is False
            assert db.execute("""
                SELECT id, organization_id, row_count
                FROM auto_vehicle_migration_batch LIMIT 1
            """).description is not None
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                with db.transaction():
                    db.execute("SELECT vin FROM auto_vehicle_migration_line LIMIT 1")
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                with db.transaction():
                    db.execute("SELECT * FROM auto_vehicle_migration_batch LIMIT 1")
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                with db.transaction():
                    db.execute("""
                        INSERT INTO auto_vehicle_migration_batch
                          (id, organization_id, source_sha256, row_count)
                        VALUES (%s,%s,%s,1)
                    """, (uuid4(),uuid4(),"a"*64))


def test_migration_auditor_reads_only_batch_metadata_not_vin(tmp_path):
    with psycopg.connect(DSN) as db:
        org, loc, _ = _org_and_bay(db)
        batch = uuid4()
        db.execute("""
            INSERT INTO auto_vehicle_migration_batch
              (id,organization_id,source_sha256,row_count)
            VALUES (%s,%s,%s,1)
        """, (batch,org,"a"*64))
        with db.transaction():
            db.execute("SET LOCAL ROLE roboport_vehicle_migration_auditor")
            assert db.execute("""
                SELECT organization_id,row_count
                FROM auto_vehicle_migration_batch WHERE id=%s
            """, (batch,)).fetchone() == (org,1)
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                with db.transaction():
                    db.execute("SELECT vin FROM auto_vehicle_migration_line WHERE batch_id=%s",
                               (batch,))
