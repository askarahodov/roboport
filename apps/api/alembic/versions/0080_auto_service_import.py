"""Synthetic service catalog import; isolated from work orders and invoicing.

Revision ID: 0080_auto_service_import
Revises: 0079_vehicle_audit_role
"""
from alembic import op

revision = "0080_auto_service_import"
down_revision = "0079_vehicle_audit_role"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
      CREATE TABLE auto_service_import_batch (
        id uuid PRIMARY KEY,
        organization_id uuid NOT NULL REFERENCES auto_organization(id),
        location_id uuid NOT NULL REFERENCES auto_service_location(id),
        source_sha256 char(64) NOT NULL CHECK(source_sha256 ~ '^[a-f0-9]{64}$'),
        row_count integer NOT NULL CHECK(row_count BETWEEN 1 AND 50000),
        created_at timestamptz NOT NULL DEFAULT now()
      )
    """)
    op.execute("""
      CREATE TABLE auto_service_import_line (
        batch_id uuid NOT NULL REFERENCES auto_service_import_batch(id),
        row_number integer NOT NULL CHECK(row_number BETWEEN 2 AND 50001),
        source_system text NOT NULL CHECK(source_system ~ '^[A-Z0-9_.-]{3,32}$'),
        source_id text NOT NULL CHECK(source_id ~ '^[A-Z0-9_.-]{1,64}$'),
        code text NOT NULL CHECK(code ~ '^[A-Z0-9_.-]{3,64}$'),
        title text NOT NULL CHECK(length(title) BETWEEN 1 AND 120),
        duration_minutes integer NOT NULL CHECK(duration_minutes BETWEEN 5 AND 1440),
        PRIMARY KEY(batch_id,row_number),
        UNIQUE(batch_id,source_system,source_id),
        UNIQUE(batch_id,code)
      )
    """)
    op.execute("""
      CREATE FUNCTION guard_auto_service_import_line()
      RETURNS trigger LANGUAGE plpgsql AS $fn$
      BEGIN
        IF NOT EXISTS(
          SELECT 1 FROM auto_service_import_batch b
          JOIN auto_service_location l ON l.id=b.location_id
          JOIN auto_organization o ON o.id=b.organization_id
          WHERE b.id=NEW.batch_id AND l.organization_id=o.id
          AND l.disabled_at IS NULL AND o.disabled_at IS NULL
          FOR SHARE OF b,l,o
        ) THEN RAISE EXCEPTION 'service import scope unavailable'; END IF;
        RETURN NEW;
      END $fn$
    """)
    op.execute("""
      CREATE TRIGGER auto_service_import_line_scope
      BEFORE INSERT ON auto_service_import_line FOR EACH ROW
      EXECUTE FUNCTION guard_auto_service_import_line()
    """)
    for table in ("auto_service_import_batch", "auto_service_import_line"):
        op.execute(f"""
          CREATE TRIGGER {table}_immutable
          BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW
          EXECUTE FUNCTION immutable_auto_stock_event()
        """)
        op.execute(f"REVOKE ALL ON {table} FROM PUBLIC")


def downgrade():
    # Disposable synthetic CI only. Never destroy business data in production.
    op.execute("DROP TABLE auto_service_import_line")
    op.execute("DROP TABLE auto_service_import_batch")
    op.execute("DROP FUNCTION guard_auto_service_import_line()")
