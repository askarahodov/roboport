"""Synthetic service catalog activation with immutable source lineage.

Revision ID: 0081_auto_service_catalog
Revises: 0080_auto_service_import
"""
from alembic import op

revision = "0081_auto_service_catalog"
down_revision = "0080_auto_service_import"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
      CREATE TABLE auto_service_catalog (
        id uuid PRIMARY KEY,
        organization_id uuid NOT NULL REFERENCES auto_organization(id),
        location_id uuid NOT NULL REFERENCES auto_service_location(id),
        code text NOT NULL CHECK (code ~ '^[A-Z0-9_.-]{3,64}$'),
        title text NOT NULL CHECK(length(title) BETWEEN 1 AND 120),
        duration_minutes integer NOT NULL CHECK(duration_minutes BETWEEN 5 AND 1440),
        created_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE(location_id,code)
      )
    """)
    op.execute("""
      CREATE TABLE auto_service_import_activation (
        batch_id uuid NOT NULL,
        row_number integer NOT NULL,
        service_id uuid NOT NULL UNIQUE REFERENCES auto_service_catalog(id),
        created_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY(batch_id,row_number),
        FOREIGN KEY(batch_id,row_number)
          REFERENCES auto_service_import_line(batch_id,row_number)
      )
    """)
    op.execute("""
      CREATE FUNCTION guard_auto_service_activation()
      RETURNS trigger LANGUAGE plpgsql AS $fn$
      BEGIN
        IF NOT EXISTS(
          SELECT 1 FROM auto_service_import_line l
          JOIN auto_service_import_batch b ON b.id=l.batch_id
          JOIN auto_service_catalog c ON c.id=NEW.service_id
          JOIN auto_service_location loc ON loc.id=b.location_id
          JOIN auto_organization o ON o.id=b.organization_id
          WHERE l.batch_id=NEW.batch_id AND l.row_number=NEW.row_number
            AND c.organization_id=b.organization_id
            AND c.location_id=b.location_id
            AND loc.organization_id=b.organization_id
            AND loc.disabled_at IS NULL AND o.disabled_at IS NULL
            AND c.code=l.code AND c.title=l.title
            AND c.duration_minutes=l.duration_minutes
          FOR SHARE OF l,b,c,loc,o
        ) THEN
          RAISE EXCEPTION 'service activation lineage mismatch';
        END IF;
        RETURN NEW;
      END $fn$
    """)
    op.execute("""
      CREATE TRIGGER auto_service_import_activation_guard
      BEFORE INSERT ON auto_service_import_activation
      FOR EACH ROW EXECUTE FUNCTION guard_auto_service_activation()
    """)
    for table in ("auto_service_catalog","auto_service_import_activation"):
        op.execute(f"""
          CREATE TRIGGER {table}_immutable
          BEFORE UPDATE OR DELETE ON {table}
          FOR EACH ROW EXECUTE FUNCTION immutable_auto_stock_event()
        """)
        op.execute(f"REVOKE ALL ON {table} FROM PUBLIC")


def downgrade():
    op.execute("DROP TABLE auto_service_import_activation")
    op.execute("DROP TABLE auto_service_catalog")
    op.execute("DROP FUNCTION guard_auto_service_activation()")
