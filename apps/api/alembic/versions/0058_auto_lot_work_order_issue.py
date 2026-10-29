"""Atomic work order issue from a warehouse lot (synthetic PG17).

Revision ID: 0058_auto_lot_work_order_issue
Revises: 0057_auto_lot_transfer
"""
from alembic import op

revision = "0058_auto_lot_work_order_issue"
down_revision = "0057_auto_lot_transfer"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
      CREATE TABLE auto_stock_lot_work_order_issue (
        id uuid PRIMARY KEY,
        request_id uuid NOT NULL UNIQUE,
        work_order_id uuid NOT NULL REFERENCES auto_work_order(id),
        lot_id uuid NOT NULL REFERENCES auto_stock_lot(id),
        sku_id uuid NOT NULL REFERENCES auto_stock_sku(id),
        quantity bigint NOT NULL CHECK (quantity BETWEEN 1 AND 1000000000),
        lot_debit_id uuid NOT NULL UNIQUE REFERENCES auto_stock_lot_allocation(id),
        stock_event_id uuid NOT NULL UNIQUE REFERENCES auto_stock_event(id),
        created_at timestamptz NOT NULL DEFAULT now()
      )
    """)
    op.execute("""
      CREATE TRIGGER auto_stock_lot_work_order_issue_immutable
      BEFORE UPDATE OR DELETE ON auto_stock_lot_work_order_issue
      FOR EACH ROW EXECUTE FUNCTION immutable_auto_stock_event()
    """)
    op.execute("""
      CREATE FUNCTION check_auto_stock_lot_work_order_issue()
      RETURNS trigger LANGUAGE plpgsql AS $fn$
      BEGIN
        IF NOT EXISTS (
          SELECT 1 FROM auto_stock_lot_allocation d
          JOIN auto_stock_event e ON e.id=NEW.stock_event_id
          JOIN auto_work_order_stock_event w ON w.stock_event_id=e.id
          WHERE d.id=NEW.lot_debit_id AND d.lot_id=NEW.lot_id
            AND d.sku_id=NEW.sku_id AND d.action='DEALLOCATE'
            AND d.quantity=NEW.quantity AND e.sku_id=NEW.sku_id
            AND e.request_id=NEW.request_id AND e.action='ISSUE'
            AND e.quantity=NEW.quantity AND w.work_order_id=NEW.work_order_id
            AND w.sku_id=NEW.sku_id AND w.action='ISSUE'
            AND w.quantity=NEW.quantity
        ) THEN
          RAISE EXCEPTION 'invalid lot work order issue pairing';
        END IF;
        RETURN NULL;
      END $fn$
    """)
    op.execute("""
      CREATE CONSTRAINT TRIGGER auto_stock_lot_work_order_issue_pair
      AFTER INSERT ON auto_stock_lot_work_order_issue
      DEFERRABLE INITIALLY DEFERRED
      FOR EACH ROW EXECUTE FUNCTION check_auto_stock_lot_work_order_issue()
    """)
    op.execute("""
      CREATE FUNCTION record_auto_stock_lot_work_order_issue(
        p_order uuid,p_lot uuid,p_request uuid,p_quantity bigint
      ) RETURNS uuid LANGUAGE plpgsql AS $fn$
      DECLARE lot_row record; order_row record; prior record;
              debit uuid; stock_event uuid; issue_id uuid; lot_qty bigint;
      BEGIN
        IF p_order IS NULL OR p_lot IS NULL OR p_request IS NULL OR p_quantity IS NULL
           OR p_quantity<1 OR p_quantity>1000000000 THEN
          RAISE EXCEPTION 'invalid lot work order issue';
        END IF;
        SELECT l.sku_id,l.organization_id,l.location_id INTO lot_row
          FROM auto_stock_lot l
          JOIN auto_service_location loc ON loc.id=l.location_id
            AND loc.organization_id=l.organization_id
          JOIN auto_organization org ON org.id=l.organization_id
          WHERE l.id=p_lot AND loc.disabled_at IS NULL AND org.disabled_at IS NULL;
        IF NOT FOUND THEN RAISE EXCEPTION 'lot unavailable'; END IF;
        -- Same order as existing services: work order scope, then SKU lock.
        SELECT w.id INTO order_row FROM auto_work_order w
          JOIN auto_appointment a ON a.id=w.appointment_id
          JOIN auto_bay_booking b ON b.id=a.booking_id
          WHERE w.id=p_order AND w.organization_id=lot_row.organization_id
            AND w.location_id=lot_row.location_id
            AND a.organization_id=w.organization_id
            AND a.location_id=w.location_id AND b.canceled_at IS NULL
          FOR SHARE OF w,a,b;
        IF NOT FOUND THEN RAISE EXCEPTION 'work order scope unavailable'; END IF;
        PERFORM 1 FROM auto_stock_sku WHERE id=lot_row.sku_id FOR UPDATE;
        SELECT * INTO prior FROM auto_stock_lot_work_order_issue WHERE request_id=p_request;
        IF FOUND THEN
          IF prior.work_order_id=p_order AND prior.lot_id=p_lot
             AND prior.sku_id=lot_row.sku_id AND prior.quantity=p_quantity THEN
            RETURN prior.id;
          END IF;
          RAISE EXCEPTION 'lot issue request replay mismatch';
        END IF;
        -- Reserve must be held by this order, not some other order.
        IF (SELECT COALESCE(SUM(CASE WHEN action='RESERVE' THEN quantity
                                     WHEN action IN ('ISSUE','RELEASE') THEN -quantity
                                     ELSE 0 END),0)
            FROM auto_work_order_stock_event
            WHERE work_order_id=p_order AND sku_id=lot_row.sku_id) < p_quantity THEN
          RAISE EXCEPTION 'work order reservation insufficient';
        END IF;
        SELECT allocated INTO lot_qty FROM auto_stock_lot_balance WHERE lot_id=p_lot;
        IF lot_qty<p_quantity THEN RAISE EXCEPTION 'lot allocation insufficient'; END IF;
        -- First deallocate, then consume the physical stock and work order
        -- reservation in the SAME transaction: rollback restores all legs.
        debit:=record_auto_stock_lot_allocation(
          p_lot,gen_random_uuid(),'DEALLOCATE',p_quantity);
        stock_event:=record_auto_work_order_stock(
          p_order,lot_row.sku_id,p_request,'ISSUE',p_quantity);
        issue_id:=gen_random_uuid();
        INSERT INTO auto_stock_lot_work_order_issue
          (id,request_id,work_order_id,lot_id,sku_id,quantity,lot_debit_id,stock_event_id)
          VALUES(issue_id,p_request,p_order,p_lot,lot_row.sku_id,
                 p_quantity,debit,stock_event);
        RETURN issue_id;
      END $fn$
    """)
    op.execute("REVOKE ALL ON FUNCTION record_auto_stock_lot_work_order_issue(uuid,uuid,uuid,bigint) FROM PUBLIC")


def downgrade():
    # Disposable synthetic PG17 only; destroys the issue pairing journal.
    op.execute("DROP FUNCTION record_auto_stock_lot_work_order_issue(uuid,uuid,uuid,bigint)")
    op.execute("DROP TABLE auto_stock_lot_work_order_issue")
    op.execute("DROP FUNCTION check_auto_stock_lot_work_order_issue()")
