"""Protect recorded serial identities against later lot quantity reductions.

Revision ID: 0071_auto_serial_quantity_guard
Revises: 0070_auto_stock_reclassification
"""
from alembic import op

revision = "0071_auto_serial_quantity_guard"
down_revision = "0070_auto_stock_reclassification"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE OR REPLACE FUNCTION guard_auto_stock_lot_allocation()
      RETURNS trigger LANGUAGE plpgsql AS $fn$
      DECLARE scope record; allocated_qty bigint; stock record; serial_count bigint;
      BEGIN
        IF TG_OP <> 'INSERT' THEN
          RAISE EXCEPTION 'lot allocation immutable'; END IF;
        SELECT l.sku_id INTO scope FROM auto_stock_lot l WHERE l.id=NEW.lot_id;
        IF NOT FOUND OR scope.sku_id IS DISTINCT FROM NEW.sku_id THEN
          RAISE EXCEPTION 'foreign lot sku'; END IF;
        PERFORM 1 FROM auto_stock_sku WHERE id=NEW.sku_id FOR UPDATE;
        SELECT COALESCE(SUM(CASE WHEN action='ALLOCATE' THEN quantity
                                 ELSE -quantity END),0)::bigint
          INTO allocated_qty FROM auto_stock_lot_allocation WHERE lot_id=NEW.lot_id;
        SELECT on_hand,
          (SELECT COALESCE(SUM(allocated),0)::bigint FROM auto_stock_lot_balance
           WHERE sku_id=NEW.sku_id) total_allocated
          INTO stock FROM auto_stock_balance WHERE sku_id=NEW.sku_id;
        IF (NEW.action='DEALLOCATE' AND allocated_qty<NEW.quantity)
          OR (NEW.action='ALLOCATE' AND stock.on_hand-stock.total_allocated<NEW.quantity)
        THEN RAISE EXCEPTION 'insufficient unallocated or lot stock'; END IF;
        -- Serial identities represent concrete pieces: allocated stock must not
        -- fall below the recorded count, including direct owner-level SQL writes.
        SELECT count(*) INTO serial_count FROM auto_stock_serial
          WHERE lot_id=NEW.lot_id;
        IF NEW.action='DEALLOCATE' AND allocated_qty-NEW.quantity<serial_count
        THEN RAISE EXCEPTION 'cannot deallocate registered serial inventory'; END IF;
        RETURN NEW;
      END $fn$""")


def downgrade():
    op.execute("""CREATE OR REPLACE FUNCTION guard_auto_stock_lot_allocation()
      RETURNS trigger LANGUAGE plpgsql AS $fn$
      DECLARE scope record; allocated_qty bigint; stock record;
      BEGIN
        IF TG_OP <> 'INSERT' THEN RAISE EXCEPTION 'lot allocation immutable'; END IF;
        SELECT l.sku_id INTO scope FROM auto_stock_lot l WHERE l.id=NEW.lot_id;
        IF NOT FOUND OR scope.sku_id IS DISTINCT FROM NEW.sku_id
        THEN RAISE EXCEPTION 'foreign lot sku'; END IF;
        PERFORM 1 FROM auto_stock_sku WHERE id=NEW.sku_id FOR UPDATE;
        SELECT COALESCE(SUM(CASE WHEN action='ALLOCATE' THEN quantity
                                 ELSE -quantity END),0)::bigint
          INTO allocated_qty FROM auto_stock_lot_allocation WHERE lot_id=NEW.lot_id;
        SELECT on_hand,
          (SELECT COALESCE(SUM(allocated),0)::bigint FROM auto_stock_lot_balance
           WHERE sku_id=NEW.sku_id) total_allocated
          INTO stock FROM auto_stock_balance WHERE sku_id=NEW.sku_id;
        IF (NEW.action='DEALLOCATE' AND allocated_qty<NEW.quantity)
           OR (NEW.action='ALLOCATE' AND stock.on_hand-stock.total_allocated<NEW.quantity)
        THEN RAISE EXCEPTION 'insufficient unallocated or lot stock'; END IF;
        RETURN NEW;
      END $fn$""")
