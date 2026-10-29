"""Return consumed parts to original lot with paired immutable provenance.

Revision ID: 0059_auto_lot_work_order_return
Revises: 0058_auto_lot_work_order_issue
"""
from alembic import op

revision = "0059_auto_lot_work_order_return"
down_revision = "0058_auto_lot_work_order_issue"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
      CREATE TABLE auto_stock_lot_work_order_return (
        id uuid PRIMARY KEY,
        request_id uuid NOT NULL UNIQUE,
        work_order_id uuid NOT NULL REFERENCES auto_work_order(id),
        lot_id uuid NOT NULL REFERENCES auto_stock_lot(id),
        sku_id uuid NOT NULL REFERENCES auto_stock_sku(id),
        quantity bigint NOT NULL CHECK (quantity BETWEEN 1 AND 1000000000),
        lot_credit_id uuid NOT NULL UNIQUE REFERENCES auto_stock_lot_allocation(id),
        stock_event_id uuid NOT NULL UNIQUE REFERENCES auto_stock_event(id),
        created_at timestamptz NOT NULL DEFAULT now()
      )
    """)
    op.execute("""
      CREATE TRIGGER auto_stock_lot_work_order_return_immutable
      BEFORE UPDATE OR DELETE ON auto_stock_lot_work_order_return
      FOR EACH ROW EXECUTE FUNCTION immutable_auto_stock_event()
    """)
    op.execute("""
      CREATE FUNCTION check_auto_stock_lot_work_order_return()
      RETURNS trigger LANGUAGE plpgsql AS $fn$
      BEGIN
        IF NOT EXISTS (
          SELECT 1 FROM auto_stock_lot_allocation c
          JOIN auto_stock_event e ON e.id=NEW.stock_event_id
          JOIN auto_work_order_stock_event w ON w.stock_event_id=e.id
          WHERE c.id=NEW.lot_credit_id AND c.lot_id=NEW.lot_id
            AND c.sku_id=NEW.sku_id AND c.action='ALLOCATE'
            AND c.quantity=NEW.quantity AND e.sku_id=NEW.sku_id
            AND e.request_id=NEW.request_id AND e.action='RETURN'
            AND e.quantity=NEW.quantity AND w.work_order_id=NEW.work_order_id
            AND w.sku_id=NEW.sku_id AND w.action='RETURN'
            AND w.quantity=NEW.quantity
        ) THEN RAISE EXCEPTION 'invalid lot work order return pairing'; END IF;
        RETURN NULL;
      END $fn$
    """)
    op.execute("""
      CREATE CONSTRAINT TRIGGER auto_stock_lot_work_order_return_pair
      AFTER INSERT ON auto_stock_lot_work_order_return
      DEFERRABLE INITIALLY DEFERRED
      FOR EACH ROW EXECUTE FUNCTION check_auto_stock_lot_work_order_return()
    """)
    op.execute("""
      CREATE FUNCTION record_auto_stock_lot_work_order_return(
        p_order uuid,p_lot uuid,p_request uuid,p_quantity bigint
      ) RETURNS uuid LANGUAGE plpgsql AS $fn$
      DECLARE lot_row record; order_row record; prior record;
              available bigint; stock_event uuid; credit uuid; return_id uuid;
      BEGIN
        IF p_order IS NULL OR p_lot IS NULL OR p_request IS NULL
           OR p_quantity IS NULL OR p_quantity<1 OR p_quantity>1000000000 THEN
          RAISE EXCEPTION 'invalid lot work order return';
        END IF;
        SELECT l.sku_id,l.organization_id,l.location_id INTO lot_row
          FROM auto_stock_lot l
          JOIN auto_service_location loc ON loc.id=l.location_id
            AND loc.organization_id=l.organization_id
          JOIN auto_organization org ON org.id=l.organization_id
          WHERE l.id=p_lot AND loc.disabled_at IS NULL AND org.disabled_at IS NULL
          FOR SHARE OF l,loc,org;
        IF NOT FOUND THEN RAISE EXCEPTION 'lot unavailable'; END IF;
        SELECT w.id INTO order_row FROM auto_work_order w
          JOIN auto_appointment a ON a.id=w.appointment_id
          JOIN auto_bay_booking b ON b.id=a.booking_id
          WHERE w.id=p_order AND w.organization_id=lot_row.organization_id
            AND w.location_id=lot_row.location_id
            AND a.organization_id=w.organization_id AND a.location_id=w.location_id
            AND b.canceled_at IS NULL
          FOR SHARE OF w,a,b;
        IF NOT FOUND THEN RAISE EXCEPTION 'work order scope unavailable'; END IF;
        PERFORM 1 FROM auto_stock_sku WHERE id=lot_row.sku_id FOR UPDATE;
        SELECT * INTO prior FROM auto_stock_lot_work_order_return WHERE request_id=p_request;
        IF FOUND THEN
          IF prior.work_order_id=p_order AND prior.lot_id=p_lot
             AND prior.sku_id=lot_row.sku_id AND prior.quantity=p_quantity THEN
            RETURN prior.id;
          END IF;
          RAISE EXCEPTION 'lot return request replay mismatch';
        END IF;
        SELECT COALESCE(SUM(q),0)::bigint INTO available FROM (
          SELECT quantity AS q FROM auto_stock_lot_work_order_issue
            WHERE work_order_id=p_order AND lot_id=p_lot AND sku_id=lot_row.sku_id
          UNION ALL
          SELECT -quantity AS q FROM auto_stock_lot_work_order_return
            WHERE work_order_id=p_order AND lot_id=p_lot AND sku_id=lot_row.sku_id
        ) quantities;
        IF available<p_quantity THEN RAISE EXCEPTION 'lot return exceeds net issues'; END IF;
        -- Credit physical stock first, then place the returned units in the lot.
        -- Both legs and the linking record are committed or rolled back together.
        stock_event:=record_auto_work_order_return(
          p_order,lot_row.sku_id,p_request,p_quantity);
        credit:=record_auto_stock_lot_allocation(
          p_lot,gen_random_uuid(),'ALLOCATE',p_quantity);
        return_id:=gen_random_uuid();
        INSERT INTO auto_stock_lot_work_order_return
          (id,request_id,work_order_id,lot_id,sku_id,quantity,lot_credit_id,stock_event_id)
          VALUES(return_id,p_request,p_order,p_lot,lot_row.sku_id,
                 p_quantity,credit,stock_event);
        RETURN return_id;
      END $fn$
    """)
    op.execute("REVOKE ALL ON FUNCTION record_auto_stock_lot_work_order_return(uuid,uuid,uuid,bigint) FROM PUBLIC")


def downgrade():
    # Disposable synthetic PG17 only.
    op.execute("DROP FUNCTION record_auto_stock_lot_work_order_return(uuid,uuid,uuid,bigint)")
    op.execute("DROP TABLE auto_stock_lot_work_order_return")
    op.execute("DROP FUNCTION check_auto_stock_lot_work_order_return()")
