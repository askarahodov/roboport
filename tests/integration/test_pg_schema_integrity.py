"""Disposability and PostgreSQL 17 schema integrity acceptance regression.

Uses only ROBOport_DATABASE_URL provided by CI; no fallback credentials.
"""
import psycopg
import pytest
from uuid import uuid4
from test_pg_baseline import DSN


def test_active_acl_and_role_partial_indexes_exist():
    with psycopg.connect(DSN) as db:
        indexes=dict(db.execute("""
          SELECT indexname,indexdef FROM pg_indexes
          WHERE schemaname='public' AND indexname IN
          ('ix_user_role_active','ix_acl_subject_ticket')
        """).fetchall())
        assert set(indexes)=={'ix_user_role_active','ix_acl_subject_ticket'}
        for definition in indexes.values():
            assert 'WHERE (revoked_at IS NULL)' in definition or 'WHERE revoked_at IS NULL' in definition
        version=db.execute("SELECT version()").fetchone()[0]
        assert 'PostgreSQL 17.' in version, version


def test_exact_model_identity_unique_and_foreign_key_negative():
    with psycopg.connect(DSN) as db:
        model,other=uuid4(),uuid4()
        fields=(model,'SyntheticBrand','SyntheticModel','SKU-REV','Dock-A','RU','R1')
        db.execute("""INSERT INTO catalog_model
            (id,brand,model,variant,dock,region,revision)
            VALUES(%s,%s,%s,%s,%s,%s,%s)""",fields)
        with pytest.raises(psycopg.errors.UniqueViolation):
            with db.transaction():
                db.execute("""INSERT INTO catalog_model
                    (id,brand,model,variant,dock,region,revision)
                    VALUES(%s,%s,%s,%s,%s,%s,%s)""",(other,*fields[1:]))
        # A different revision is a distinct exact variant, not an implicit match.
        db.execute("""INSERT INTO catalog_model
            (id,brand,model,variant,dock,region,revision)
            VALUES(%s,%s,%s,%s,%s,%s,%s)""",(other,*fields[1:-1],'R2'))
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            with db.transaction():
                db.execute("""INSERT INTO service_capability(id,model_id,capability,evidence)
                VALUES(%s,%s,'REPAIR','synthetic')""",(uuid4(),uuid4()))
