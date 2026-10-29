"""Synthetic warehouse bins and stock lot identities.

Revision ID: 0055_auto_stock_bin_lot
Revises: 0054_auto_work_order_return
"""
from alembic import op

revision = "0055_auto_stock_bin_lot"
down_revision = "0054_auto_work_order_return"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
      CREATE TABLE auto_stock_bin (
        id uuid PRIMARY KEY,
        organization_id uuid NOT NULL REFERENCES auto_organization(id),
        location_id uuid NOT NULL REFERENCES auto_service_location(id),
        code text NOT NULL CHECK (code ~ '^[A-Z0-9][A-Z0-9_-]{1,31}$'),
        created_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE(organization_id,location_id,code),
        UNIQUE(id,organization_id,location_id)
      )
    """)
    op.execute("""
      CREATE TABLE auto_stock_lot (
        id uuid PRIMARY KEY,
        organization_id uuid NOT NULL,
        location_id uuid NOT NULL,
        sku_id uuid NOT NULL REFERENCES auto_stock_sku(id),
        bin_id uuid NOT NULL,
        batch_code text NOT NULL CHECK (length(btrim(batch_code)) BETWEEN 1 AND 80),
        source_revision text NOT NULL CHECK (length(btrim(source_revision)) BETWEEN 1 AND 160),
        created_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE(sku_id,bin_id,batch_code),
        FOREIGN KEY(bin_id,organization_id,location_id)
          REFERENCES auto_stock_bin(id,organization_id,location_id)
      )
    """)
    op.execute("""
      CREATE FUNCTION guard_auto_stock_bin() RETURNS trigger LANGUAGE plpgsql AS $fn$
      BEGIN
        IF TG_OP <> 'INSERT' THEN
          RAISE EXCEPTION 'stock bin immutable';
        END IF;
        IF NOT EXISTS (
          SELECT 1 FROM auto_service_location l
          JOIN auto_organization o ON o.id=l.organization_id
          WHERE l.id=NEW.location_id AND o.id=NEW.organization_id
            AND l.disabled_at IS NULL AND o.disabled_at IS NULL
          FOR SHARE OF l,o
        ) THEN
          RAISE EXCEPTION 'stock bin outside enabled organization';
        END IF;
        RETURN NEW;
      END $fn$
    """)
    op.execute("""
      CREATE TRIGGER auto_stock_bin_guard BEFORE INSERT OR UPDATE OR DELETE
      ON auto_stock_bin FOR EACH ROW EXECUTE FUNCTION guard_auto_stock_bin()
    """)
    op.execute("""
      CREATE FUNCTION guard_auto_stock_lot() RETURNS trigger LANGUAGE plpgsql AS $fn$
      BEGIN
        IF TG_OP <> 'INSERT' THEN
          RAISE EXCEPTION 'stock lot immutable';
        END IF;
        IF NOT EXISTS (
          SELECT 1 FROM auto_stock_sku s
          JOIN auto_service_location l ON l.id=s.location_id
          JOIN auto_organization o ON o.id=s.organization_id
          WHERE s.id=NEW.sku_id AND s.organization_id=NEW.organization_id
            AND s.location_id=NEW.location_id AND l.disabled_at IS NULL
            AND o.disabled_at IS NULL AND l.organization_id=o.id
          FOR SHARE OF s,l,o
        ) THEN
          RAISE EXCEPTION 'stock lot sku outside enabled organization';
        END IF;
        RETURN NEW;
      END $fn$
    """)
    op.execute("""
      CREATE TRIGGER auto_stock_lot_guard BEFORE INSERT OR UPDATE OR DELETE
      ON auto_stock_lot FOR EACH ROW EXECUTE FUNCTION guard_auto_stock_lot()
    """)


def downgrade():
    # Disposable PG17 test schema only; destroys bin and lot identities.
    op.execute("DROP TABLE auto_stock_lot")
    op.execute("DROP FUNCTION guard_auto_stock_lot()")
    op.execute("DROP TABLE auto_stock_bin")
    op.execute("DROP FUNCTION guard_auto_stock_bin()")
