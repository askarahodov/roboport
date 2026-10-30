"""Isolated synthetic-only PostgreSQL migration staging (no live stock writes).

Revision ID: 0074_auto_migration_staging
Revises: 0073_auto_order_financial
"""
from alembic import op

revision = "0074_auto_migration_staging"
down_revision = "0073_auto_order_financial"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
      CREATE TABLE auto_migration_staging_batch (
        id uuid PRIMARY KEY,
        organization_id uuid NOT NULL REFERENCES auto_organization(id),
        source_sha256 char(64) NOT NULL CHECK (source_sha256 ~ '^[0-9a-f]{64}$'),
        row_count integer NOT NULL CHECK (row_count BETWEEN 1 AND 50000),
        sku_count integer NOT NULL CHECK (sku_count BETWEEN 0 AND 50000),
        stock_count integer NOT NULL CHECK (stock_count BETWEEN 0 AND 50000),
        created_at timestamptz NOT NULL DEFAULT now(),
        CHECK (row_count = sku_count + stock_count),
        UNIQUE (organization_id, id)
      )
    """)
    op.execute("""
      CREATE TABLE auto_migration_staging_line (
        batch_id uuid NOT NULL REFERENCES auto_migration_staging_batch(id),
        line_number integer NOT NULL CHECK (line_number BETWEEN 2 AND 50001),
        organization_id uuid NOT NULL REFERENCES auto_organization(id),
        location_id uuid NOT NULL REFERENCES auto_service_location(id),
        record_type text NOT NULL CHECK (record_type IN ('SKU','STOCK')),
        source_id text NOT NULL CHECK (source_id ~ '^[A-Z0-9_.-]{1,64}$'),
        article text NOT NULL CHECK (article ~ '^[A-Z0-9_.-]{3,64}$'),
        quantity bigint NOT NULL CHECK (quantity BETWEEN 0 AND 1000000000),
        PRIMARY KEY (batch_id, line_number),
        UNIQUE (batch_id, organization_id, location_id, source_id),
        CHECK (record_type <> 'SKU' OR quantity = 0)
      )
    """)
    op.execute("""
      CREATE FUNCTION guard_auto_migration_staging_line()
      RETURNS trigger LANGUAGE plpgsql AS $fn$
      BEGIN
        IF NOT EXISTS (
          SELECT 1 FROM auto_migration_staging_batch b
          WHERE b.id=NEW.batch_id AND b.organization_id=NEW.organization_id
          FOR SHARE
        ) OR NOT EXISTS (
          SELECT 1 FROM auto_service_location l
          JOIN auto_organization o ON o.id=l.organization_id
          WHERE l.id=NEW.location_id AND l.organization_id=NEW.organization_id
            AND l.disabled_at IS NULL AND o.disabled_at IS NULL
          FOR SHARE OF l,o
        ) THEN
          RAISE EXCEPTION 'migration staging scope unavailable';
        END IF;
        RETURN NEW;
      END $fn$
    """)
    op.execute("""
      CREATE TRIGGER auto_migration_staging_scope BEFORE INSERT
      ON auto_migration_staging_line FOR EACH ROW
      EXECUTE FUNCTION guard_auto_migration_staging_line()
    """)
    for table in ("auto_migration_staging_batch", "auto_migration_staging_line"):
        op.execute(f"""CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE
          ON {table} FOR EACH ROW EXECUTE FUNCTION immutable_auto_stock_event()""")
        op.execute(f"REVOKE ALL ON {table} FROM PUBLIC")


def downgrade():
    # Disposable synthetic CI databases only; never drop staging records in production.
    op.execute("DROP TABLE auto_migration_staging_line")
    op.execute("DROP TABLE auto_migration_staging_batch")
    op.execute("DROP FUNCTION guard_auto_migration_staging_line()")
