"""Quantitative allocations of existing stock to immutable warehouse lots.

Revision ID: 0056_auto_lot_allocation
Revises: 0055_auto_stock_bin_lot
"""
from alembic import op

revision = "0056_auto_lot_allocation"
down_revision = "0055_auto_stock_bin_lot"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
      CREATE TABLE auto_stock_lot_allocation (
        id uuid PRIMARY KEY,
        request_id uuid NOT NULL UNIQUE,
        lot_id uuid NOT NULL REFERENCES auto_stock_lot(id),
        sku_id uuid NOT NULL REFERENCES auto_stock_sku(id),
        action text NOT NULL CHECK (action IN ('ALLOCATE','DEALLOCATE')),
        quantity bigint NOT NULL CHECK (quantity BETWEEN 1 AND 1000000000),
        created_at timestamptz NOT NULL DEFAULT now()
      )
    """)
    op.execute("""
      CREATE INDEX ix_auto_stock_lot_allocation_lot
        ON auto_stock_lot_allocation(lot_id)
    """)
    op.execute("""
      CREATE TRIGGER auto_stock_lot_allocation_immutable
      BEFORE UPDATE OR DELETE ON auto_stock_lot_allocation
      FOR EACH ROW EXECUTE FUNCTION immutable_auto_stock_event()
    """)
    op.execute("""
      CREATE VIEW auto_stock_lot_balance AS
      SELECT l.id lot_id,l.sku_id,l.bin_id,l.organization_id,l.location_id,
             COALESCE(SUM(CASE WHEN e.action='ALLOCATE' THEN e.quantity
                               ELSE -e.quantity END),0)::bigint AS allocated
      FROM auto_stock_lot l LEFT JOIN auto_stock_lot_allocation e ON e.lot_id=l.id
      GROUP BY l.id,l.sku_id,l.bin_id,l.organization_id,l.location_id
    """)
    # Enforce even direct owner INSERT in synthetic database. All changes to
    # allocations serialize with physical SKU movement via its row lock.
    op.execute("""
      CREATE FUNCTION guard_auto_stock_lot_allocation()
      RETURNS trigger LANGUAGE plpgsql AS $fn$
      DECLARE scope record; allocated_qty bigint; stock record;
      BEGIN
        IF TG_OP <> 'INSERT' THEN
          RAISE EXCEPTION 'lot allocation immutable';
        END IF;
        SELECT l.sku_id INTO scope FROM auto_stock_lot l
          WHERE l.id=NEW.lot_id;
        IF NOT FOUND OR scope.sku_id IS DISTINCT FROM NEW.sku_id THEN
          RAISE EXCEPTION 'foreign lot sku';
        END IF;
        PERFORM 1 FROM auto_stock_sku WHERE id=NEW.sku_id FOR UPDATE;
        SELECT COALESCE(SUM(CASE WHEN action='ALLOCATE' THEN quantity
                                 ELSE -quantity END),0)::bigint
          INTO allocated_qty FROM auto_stock_lot_allocation
          WHERE lot_id=NEW.lot_id;
        SELECT on_hand,
          (SELECT COALESCE(SUM(allocated),0)::bigint FROM auto_stock_lot_balance
           WHERE sku_id=NEW.sku_id) total_allocated
          INTO stock FROM auto_stock_balance WHERE sku_id=NEW.sku_id;
        IF (NEW.action='DEALLOCATE' AND allocated_qty<NEW.quantity)
           OR (NEW.action='ALLOCATE' AND stock.on_hand-stock.total_allocated<NEW.quantity) THEN
          RAISE EXCEPTION 'insufficient unallocated or lot stock';
        END IF;
        RETURN NEW;
      END $fn$
    """)
    op.execute("""
      CREATE TRIGGER auto_stock_lot_allocation_guard BEFORE INSERT
      ON auto_stock_lot_allocation FOR EACH ROW
      EXECUTE FUNCTION guard_auto_stock_lot_allocation()
    """)
    # Existing physical ISSUE may consume only unallocated units. UPDATE of
    # aggregate balance would otherwise silently invalidate lot inventory.
    op.execute("""
      CREATE OR REPLACE FUNCTION guard_auto_stock_event_insert()
      RETURNS trigger LANGUAGE plpgsql AS $fn$
      DECLARE scope record; stock record; allocation bigint;
      BEGIN
        SELECT organization_id,location_id INTO scope
          FROM auto_stock_sku WHERE id=NEW.sku_id FOR UPDATE;
        IF NOT FOUND OR (NEW.organization_id,NEW.location_id)
            IS DISTINCT FROM (scope.organization_id,scope.location_id) THEN
          RAISE EXCEPTION 'foreign stock scope';
        END IF;
        SELECT on_hand,reserved INTO stock FROM auto_stock_balance WHERE sku_id=NEW.sku_id;
        IF (NEW.action='RESERVE' AND stock.on_hand-stock.reserved<NEW.quantity)
           OR (NEW.action IN ('RELEASE','ISSUE') AND stock.reserved<NEW.quantity) THEN
          RAISE EXCEPTION 'insufficient stock or reservation';
        END IF;
        IF NEW.action='ISSUE' THEN
          SELECT COALESCE(SUM(allocated),0)::bigint INTO allocation
          FROM auto_stock_lot_balance WHERE sku_id=NEW.sku_id;
          IF stock.on_hand-allocation<NEW.quantity THEN
            RAISE EXCEPTION 'issued quantity is allocated to lots';
          END IF;
        END IF;
        RETURN NEW;
      END $fn$
    """)
    op.execute("""
      CREATE FUNCTION record_auto_stock_lot_allocation(
        p_lot uuid,p_request uuid,p_action text,p_quantity bigint
      ) RETURNS uuid LANGUAGE plpgsql AS $fn$
      DECLARE lot_row record; existing record; event_id uuid;
      BEGIN
        IF p_lot IS NULL OR p_request IS NULL
           OR p_action IS NULL OR p_action NOT IN ('ALLOCATE','DEALLOCATE')
           OR p_quantity IS NULL OR p_quantity<1 OR p_quantity>1000000000 THEN
          RAISE EXCEPTION 'invalid lot allocation';
        END IF;
        SELECT l.sku_id INTO lot_row FROM auto_stock_lot l
          JOIN auto_service_location loc ON loc.id=l.location_id
            AND loc.organization_id=l.organization_id
          JOIN auto_organization org ON org.id=l.organization_id
          WHERE l.id=p_lot AND loc.disabled_at IS NULL AND org.disabled_at IS NULL;
        IF NOT FOUND THEN RAISE EXCEPTION 'lot unavailable'; END IF;
        PERFORM 1 FROM auto_stock_sku WHERE id=lot_row.sku_id FOR UPDATE;
        SELECT * INTO existing FROM auto_stock_lot_allocation WHERE request_id=p_request;
        IF FOUND THEN
          IF existing.lot_id=p_lot AND existing.sku_id=lot_row.sku_id
             AND existing.action=p_action AND existing.quantity=p_quantity THEN
            RETURN existing.id;
          END IF;
          RAISE EXCEPTION 'lot request replay mismatch';
        END IF;
        event_id:=gen_random_uuid();
        INSERT INTO auto_stock_lot_allocation
          (id,request_id,lot_id,sku_id,action,quantity)
          VALUES(event_id,p_request,p_lot,lot_row.sku_id,p_action,p_quantity);
        RETURN event_id;
      END $fn$
    """)
    op.execute("REVOKE ALL ON FUNCTION record_auto_stock_lot_allocation(uuid,uuid,text,bigint) FROM PUBLIC")


def downgrade():
    # Disposable synthetic PG17 only; destroys allocation history.
    op.execute("DROP FUNCTION record_auto_stock_lot_allocation(uuid,uuid,text,bigint)")
    op.execute("""
      CREATE OR REPLACE FUNCTION guard_auto_stock_event_insert()
      RETURNS trigger LANGUAGE plpgsql AS $fn$
      DECLARE scope record; stock record;
      BEGIN
        SELECT organization_id,location_id INTO scope
          FROM auto_stock_sku WHERE id=NEW.sku_id FOR UPDATE;
        IF NOT FOUND OR (NEW.organization_id,NEW.location_id)
            IS DISTINCT FROM (scope.organization_id,scope.location_id) THEN
          RAISE EXCEPTION 'foreign stock scope';
        END IF;
        SELECT on_hand,reserved INTO stock FROM auto_stock_balance WHERE sku_id=NEW.sku_id;
        IF (NEW.action='RESERVE' AND stock.on_hand-stock.reserved<NEW.quantity)
           OR (NEW.action IN ('RELEASE','ISSUE') AND stock.reserved<NEW.quantity) THEN
          RAISE EXCEPTION 'insufficient stock or reservation';
        END IF;
        RETURN NEW;
      END $fn$
    """)
    op.execute("DROP VIEW auto_stock_lot_balance")
    op.execute("DROP TABLE auto_stock_lot_allocation")
    op.execute("DROP FUNCTION guard_auto_stock_lot_allocation()")
