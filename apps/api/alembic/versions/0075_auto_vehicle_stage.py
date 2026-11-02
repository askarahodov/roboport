"""Synthetic vehicle staging without customer ownership or repair history.

Revision ID: 0075_auto_vehicle_stage
Revises: 0074_auto_migration_staging
"""
from alembic import op

revision = "0075_auto_vehicle_stage"
down_revision = "0074_auto_migration_staging"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
      CREATE TABLE auto_vehicle_migration_batch(
        id uuid PRIMARY KEY,
        organization_id uuid NOT NULL REFERENCES auto_organization(id),
        source_sha256 char(64) NOT NULL CHECK(source_sha256 ~ '^[a-f0-9]{64}$'),
        row_count integer NOT NULL CHECK(row_count BETWEEN 1 AND 50000),
        created_at timestamptz NOT NULL DEFAULT now()
      )
    """)
    op.execute("""
      CREATE TABLE auto_vehicle_migration_line(
        batch_id uuid NOT NULL REFERENCES auto_vehicle_migration_batch(id),
        row_number integer NOT NULL CHECK(row_number BETWEEN 2 AND 50001),
        organization_id uuid NOT NULL REFERENCES auto_organization(id),
        location_id uuid NOT NULL REFERENCES auto_service_location(id),
        source_id text NOT NULL CHECK(source_id ~ '^[A-Z0-9_.-]{1,64}$'),
        source_system text NOT NULL CHECK(source_system ~ '^[A-Z0-9_.-]{3,32}$'),
        vin varchar(17) NOT NULL CHECK(vin ~ '^[A-HJ-NPR-Z0-9]{17}$'),
        make text NOT NULL CHECK(length(make) BETWEEN 1 AND 100),
        model text NOT NULL CHECK(length(model) BETWEEN 1 AND 120),
        PRIMARY KEY(batch_id,row_number),
        UNIQUE(batch_id,organization_id,location_id,source_system,source_id),
        UNIQUE(batch_id,vin)
      )
    """)
    op.execute("""
      CREATE FUNCTION guard_auto_vehicle_migration_line()
      RETURNS trigger LANGUAGE plpgsql AS $fn$
      BEGIN
        IF NOT EXISTS(
          SELECT 1 FROM auto_vehicle_migration_batch b
          WHERE b.id=NEW.batch_id AND b.organization_id=NEW.organization_id
          FOR SHARE
        ) OR NOT EXISTS(
          SELECT 1 FROM auto_service_location l
          JOIN auto_organization o ON o.id=l.organization_id
          WHERE l.id=NEW.location_id AND o.id=NEW.organization_id
          AND l.disabled_at IS NULL AND o.disabled_at IS NULL
          FOR SHARE OF l,o
        ) THEN RAISE EXCEPTION 'vehicle staging scope unavailable'; END IF;
        RETURN NEW;
      END $fn$
    """)
    op.execute("""
      CREATE TRIGGER auto_vehicle_migration_line_scope
      BEFORE INSERT ON auto_vehicle_migration_line FOR EACH ROW
      EXECUTE FUNCTION guard_auto_vehicle_migration_line()
    """)
    for name in ("auto_vehicle_migration_batch", "auto_vehicle_migration_line"):
        op.execute(f"""CREATE TRIGGER {name}_immutable
          BEFORE UPDATE OR DELETE ON {name}
          FOR EACH ROW EXECUTE FUNCTION immutable_auto_stock_event()""")
        op.execute(f"REVOKE ALL ON {name} FROM PUBLIC")


def downgrade():
    # Disposable synthetic CI only; dropping these records in production prohibited.
    op.execute("DROP TABLE auto_vehicle_migration_line")
    op.execute("DROP TABLE auto_vehicle_migration_batch")
    op.execute("DROP FUNCTION guard_auto_vehicle_migration_line()")
