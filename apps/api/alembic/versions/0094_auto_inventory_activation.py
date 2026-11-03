"""Synthetic-only SKU/STOCK staged-line activation, with immutable ledger lineage.

Revision ID: 0094_auto_inventory_activation
Revises: 0093_auto_location_opening_hours

No owner/customer/document data is imported; no public production authority.
"""
from alembic import op
from sqlalchemy import text

revision = "0094_auto_inventory_activation"
down_revision = "0093_auto_location_opening_hours"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
      CREATE TABLE auto_migration_stock_activation (
        batch_id uuid NOT NULL,
        line_number integer NOT NULL,
        record_type text NOT NULL CHECK(record_type IN ('SKU','STOCK')),
        sku_id uuid NOT NULL REFERENCES auto_stock_sku(id),
        stock_event_id uuid UNIQUE REFERENCES auto_stock_event(id),
        created_by uuid NOT NULL REFERENCES app_user(id),
        created_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY(batch_id,line_number),
        FOREIGN KEY(batch_id,line_number)
          REFERENCES auto_migration_staging_line(batch_id,line_number)
      )
    """)
    op.execute("""
      CREATE UNIQUE INDEX auto_migration_stock_activation_single_sku
      ON auto_migration_stock_activation(batch_id,sku_id)
      WHERE record_type='SKU'
    """)
    op.execute("""
      CREATE INDEX auto_migration_stock_activation_sku
      ON auto_migration_stock_activation(sku_id)
    """)
    op.execute("""
      CREATE FUNCTION guard_auto_migration_stock_activation()
      RETURNS trigger LANGUAGE plpgsql AS $fn$
      DECLARE
        source_line record;
        item record;
        stock record;
      BEGIN
        SELECT l.organization_id,l.location_id,l.record_type,l.article,
               l.quantity INTO source_line
        FROM auto_migration_staging_line l
        JOIN auto_migration_staging_batch b ON b.id=l.batch_id
          AND b.organization_id=l.organization_id
        JOIN auto_service_location loc ON loc.id=l.location_id
          AND loc.organization_id=l.organization_id
        JOIN auto_organization org ON org.id=l.organization_id
        WHERE l.batch_id=NEW.batch_id AND l.line_number=NEW.line_number
          AND loc.disabled_at IS NULL AND org.disabled_at IS NULL
        FOR SHARE OF l,b,loc,org;
        IF NOT FOUND OR NEW.record_type<>source_line.record_type THEN
          RAISE EXCEPTION 'inventory activation source unavailable';
        END IF;
        SELECT organization_id,location_id,article INTO item
        FROM auto_stock_sku WHERE id=NEW.sku_id FOR SHARE;
        IF NOT FOUND OR (item.organization_id,item.location_id,item.article)
            IS DISTINCT FROM
              (source_line.organization_id,source_line.location_id,
               source_line.article) THEN
          RAISE EXCEPTION 'inventory activation sku scope mismatch';
        END IF;
        IF source_line.record_type='SKU' THEN
          IF source_line.quantity<>0 OR NEW.stock_event_id IS NOT NULL THEN
            RAISE EXCEPTION 'inventory sku activation must not post stock';
          END IF;
        ELSE
          -- A stock line cannot cite a SKU belonging to a different batch.
          IF NOT EXISTS (
            SELECT 1 FROM auto_migration_stock_activation a
            JOIN auto_migration_staging_line line
              ON line.batch_id=a.batch_id AND line.line_number=a.line_number
            WHERE a.batch_id=NEW.batch_id AND a.sku_id=NEW.sku_id
              AND a.record_type='SKU'
              AND line.record_type='SKU'
              AND line.article=source_line.article
          ) THEN
            RAISE EXCEPTION 'inventory receipt missing batch sku lineage';
          END IF;
          IF source_line.quantity=0 THEN
            IF NEW.stock_event_id IS NOT NULL THEN
              RAISE EXCEPTION 'zero stock cannot create a receipt';
            END IF;
          ELSE
            IF NEW.stock_event_id IS NULL THEN
              RAISE EXCEPTION 'positive stock requires a receipt';
            END IF;
            SELECT sku_id,organization_id,location_id,action,quantity
              INTO stock FROM auto_stock_event
              WHERE id=NEW.stock_event_id FOR SHARE;
            IF NOT FOUND OR
               (stock.sku_id,stock.organization_id,stock.location_id,
                stock.action,stock.quantity)
               IS DISTINCT FROM
               (NEW.sku_id,source_line.organization_id,
                source_line.location_id,'RECEIPT',source_line.quantity) THEN
              RAISE EXCEPTION 'inventory receipt does not match source';
            END IF;
          END IF;
        END IF;
        RETURN NEW;
      END $fn$
    """)
    op.execute("""
      CREATE TRIGGER auto_migration_stock_activation_guard
      BEFORE INSERT ON auto_migration_stock_activation
      FOR EACH ROW EXECUTE FUNCTION guard_auto_migration_stock_activation()
    """)
    op.execute("""
      CREATE TRIGGER auto_migration_stock_activation_immutable
      BEFORE UPDATE OR DELETE ON auto_migration_stock_activation
      FOR EACH ROW EXECUTE FUNCTION immutable_auto_stock_event()
    """)
    op.execute("REVOKE ALL ON auto_migration_stock_activation FROM PUBLIC")


def downgrade():
    # Prevent a commit between the empty-table check and DROP TABLE.
    # ACCESS EXCLUSIVE conflicts with INSERT's ROW EXCLUSIVE and stays held
    # until transactional Alembic DDL commits or rolls back.
    op.execute("LOCK TABLE auto_migration_stock_activation IN ACCESS EXCLUSIVE MODE")
    if op.get_bind().execute(text(
        "SELECT 1 FROM auto_migration_stock_activation LIMIT 1"
    )).first():
        raise RuntimeError(
            "activated inventory lineage exists: recover an audited backup"
        )
    op.execute("DROP TABLE auto_migration_stock_activation")
    op.execute("DROP FUNCTION guard_auto_migration_stock_activation()")
