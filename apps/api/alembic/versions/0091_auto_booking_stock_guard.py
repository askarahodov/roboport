"""Protect material reservations across appointment cancel/reschedule (synthetic PG17).

Revision ID: 0091_auto_booking_stock_guard
Revises: 0090_auto_location_timezone

No destructive changes; immutable stock and appointment history retained.
"""
from alembic import op
from sqlalchemy import text

revision = "0091_auto_booking_stock_guard"
down_revision = "0090_auto_location_timezone"
branch_labels = None
depends_on = None


def upgrade():
    # A canceled booking must never strand an outstanding stock reservation.
    # Reschedule writes its immutable move BEFORE canceling the old booking.
    op.execute("""
      CREATE FUNCTION guard_auto_booking_material_cancel()
      RETURNS trigger LANGUAGE plpgsql AS $fn$
      DECLARE linked_order uuid;
      BEGIN
        IF OLD.canceled_at IS NOT NULL OR NEW.canceled_at IS NULL THEN
          RETURN NEW;
        END IF;
        -- A technical cancel of the previous booking inside a committed
        -- reschedule is not a customer cancellation; WorkOrder stays active.
        IF EXISTS (
          SELECT 1 FROM auto_appointment_reschedule r
          WHERE r.old_booking_id=OLD.id
        ) THEN
          RETURN NEW;
        END IF;
        SELECT w.id INTO linked_order
        FROM auto_appointment a
        JOIN auto_work_order w ON w.appointment_id=a.id
        WHERE (a.booking_id=OLD.id OR EXISTS (
          SELECT 1 FROM auto_appointment_reschedule r
          WHERE r.appointment_id=a.id AND r.new_booking_id=OLD.id
        ))
        LIMIT 1;
        IF linked_order IS NOT NULL AND EXISTS (
          SELECT 1
          FROM auto_work_order_stock_event e
          WHERE e.work_order_id=linked_order
          GROUP BY e.work_order_id
          HAVING SUM(CASE WHEN e.action='RESERVE' THEN e.quantity
                          WHEN e.action IN ('ISSUE','RELEASE') THEN -e.quantity
                          ELSE 0 END) > 0
              OR SUM(CASE WHEN e.action='ISSUE' THEN e.quantity ELSE 0 END) > 0
        ) THEN
          RAISE EXCEPTION 'work order stock must be reconciled before cancel';
        END IF;
        RETURN NEW;
      END $fn$
    """)
    op.execute("""CREATE TRIGGER auto_booking_material_cancel_guard
      BEFORE UPDATE OF canceled_at ON auto_bay_booking
      FOR EACH ROW EXECUTE FUNCTION guard_auto_booking_material_cancel()""")
    # Existing ledger function used the INITIAL booking, rejecting valid
    # RESERVE/ISSUE/RELEASE after a reschedule. Keep its security and all
    # accounting logic unchanged, replace only the current booking join.
    op.execute("""
      CREATE OR REPLACE FUNCTION record_auto_work_order_stock(
        p_order uuid, p_sku uuid, p_request uuid, p_action text, p_quantity bigint
      ) RETURNS uuid LANGUAGE plpgsql AS $fn$
      DECLARE order_row record; sku_row record; prior record; line record;
              stock record; previous_id uuid; new_id uuid;
      BEGIN
        IF p_order IS NULL OR p_sku IS NULL OR p_request IS NULL
           OR p_action IS NULL OR p_action NOT IN ('RESERVE','ISSUE','RELEASE')
           OR p_quantity IS NULL OR p_quantity < 1 OR p_quantity > 1000000000 THEN
          RAISE EXCEPTION 'invalid work order stock operation';
        END IF;
        SELECT w.organization_id,w.location_id INTO order_row
          FROM auto_work_order w
          JOIN auto_appointment a ON a.id=w.appointment_id
          JOIN LATERAL (
            SELECT COALESCE((
              SELECT rs.new_booking_id FROM auto_appointment_reschedule rs
              WHERE rs.appointment_id=a.id
              ORDER BY rs.created_at DESC,rs.id DESC LIMIT 1
            ),a.booking_id) AS current_id
          ) current_booking ON TRUE
          JOIN auto_bay_booking b ON b.id=current_booking.current_id
            AND b.organization_id=w.organization_id
            AND b.location_id=w.location_id
          JOIN auto_organization o ON o.id=w.organization_id
          JOIN auto_service_location l ON l.id=w.location_id
          WHERE w.id=p_order AND a.organization_id=w.organization_id
            AND a.location_id=w.location_id
            AND b.canceled_at IS NULL AND o.disabled_at IS NULL
            AND l.disabled_at IS NULL AND l.organization_id=w.organization_id
          FOR SHARE OF w,a,b,o,l;
        IF NOT FOUND THEN
          RAISE EXCEPTION 'work order unavailable';
        END IF;
        SELECT s.organization_id,s.location_id INTO sku_row
          FROM auto_stock_sku s WHERE s.id=p_sku FOR UPDATE;
        IF NOT FOUND OR (order_row.organization_id,order_row.location_id)
                         IS DISTINCT FROM (sku_row.organization_id,sku_row.location_id) THEN
          RAISE EXCEPTION 'work order stock scope mismatch';
        END IF;
        SELECT e.id,e.sku_id,e.action,e.quantity,wo.work_order_id
          INTO prior FROM auto_stock_event e
          LEFT JOIN auto_work_order_stock_event wo ON wo.stock_event_id=e.id
          WHERE e.request_id=p_request;
        IF FOUND THEN
          IF prior.work_order_id=p_order AND prior.sku_id=p_sku
             AND prior.action=p_action AND prior.quantity=p_quantity THEN
            RETURN prior.id;
          END IF;
          RAISE EXCEPTION 'stock request replay mismatch';
        END IF;
        SELECT COALESCE(SUM(CASE WHEN action='RESERVE' THEN quantity
                                 WHEN action IN ('ISSUE','RELEASE') THEN -quantity
                                 ELSE 0 END),0)::bigint AS outstanding
          INTO line FROM auto_work_order_stock_event
          WHERE work_order_id=p_order AND sku_id=p_sku;
        IF p_action IN ('ISSUE','RELEASE') AND line.outstanding < p_quantity THEN
          RAISE EXCEPTION 'work order reservation insufficient';
        END IF;
        SELECT on_hand,reserved INTO stock FROM auto_stock_balance
          WHERE sku_id=p_sku;
        IF p_action='RESERVE' AND stock.on_hand-stock.reserved < p_quantity THEN
          RAISE EXCEPTION 'stock unavailable';
        END IF;
        new_id := gen_random_uuid();
        INSERT INTO auto_stock_event
          (id,request_id,sku_id,organization_id,location_id,action,quantity)
          VALUES(new_id,p_request,p_sku,sku_row.organization_id,
                 sku_row.location_id,p_action,p_quantity);
        INSERT INTO auto_work_order_stock_event
          (stock_event_id,work_order_id,sku_id,action,quantity)
          VALUES(new_id,p_order,p_sku,p_action,p_quantity);
        RETURN new_id;
      END $fn$
    """)


def downgrade():
    # Do not make stock-bearing orders cancelable by silent down-migration.
    has_history = op.get_bind().execute(text("""
      SELECT 1 FROM auto_work_order_stock_event LIMIT 1
    """)).first()
    if has_history:
        raise RuntimeError("stock history exists: use audited disposable restore")
    op.execute("DROP TRIGGER auto_booking_material_cancel_guard ON auto_bay_booking")
    op.execute("DROP FUNCTION guard_auto_booking_material_cancel()")
    # The old function would incorrectly use the initial booking after move.
    # In a disposable database without stock history, restore by baseline.
    # Do not silently restore an unsafe function when there are movements.
    op.execute("""
      CREATE OR REPLACE FUNCTION record_auto_work_order_stock(
        p_order uuid, p_sku uuid, p_request uuid, p_action text, p_quantity bigint
      ) RETURNS uuid LANGUAGE plpgsql AS $fn$
      DECLARE order_row record; sku_row record; prior record; line record;
              stock record; previous_id uuid; new_id uuid;
      BEGIN
        IF p_order IS NULL OR p_sku IS NULL OR p_request IS NULL
           OR p_action IS NULL OR p_action NOT IN ('RESERVE','ISSUE','RELEASE')
           OR p_quantity IS NULL OR p_quantity < 1 OR p_quantity > 1000000000 THEN
          RAISE EXCEPTION 'invalid work order stock operation';
        END IF;
        SELECT w.organization_id,w.location_id INTO order_row
          FROM auto_work_order w
          JOIN auto_appointment a ON a.id=w.appointment_id
          JOIN auto_bay_booking b ON b.id=a.booking_id
          JOIN auto_organization o ON o.id=w.organization_id
          JOIN auto_service_location l ON l.id=w.location_id
          WHERE w.id=p_order AND a.organization_id=w.organization_id
            AND a.location_id=w.location_id
            AND b.canceled_at IS NULL AND o.disabled_at IS NULL
            AND l.disabled_at IS NULL AND l.organization_id=w.organization_id
          FOR SHARE OF w,a,b,o,l;
        IF NOT FOUND THEN
          RAISE EXCEPTION 'work order unavailable';
        END IF;
        SELECT s.organization_id,s.location_id INTO sku_row
          FROM auto_stock_sku s WHERE s.id=p_sku FOR UPDATE;
        IF NOT FOUND OR (order_row.organization_id,order_row.location_id)
                         IS DISTINCT FROM (sku_row.organization_id,sku_row.location_id) THEN
          RAISE EXCEPTION 'work order stock scope mismatch';
        END IF;
        SELECT e.id,e.sku_id,e.action,e.quantity,wo.work_order_id
          INTO prior FROM auto_stock_event e
          LEFT JOIN auto_work_order_stock_event wo ON wo.stock_event_id=e.id
          WHERE e.request_id=p_request;
        IF FOUND THEN
          IF prior.work_order_id=p_order AND prior.sku_id=p_sku
             AND prior.action=p_action AND prior.quantity=p_quantity THEN
            RETURN prior.id;
          END IF;
          RAISE EXCEPTION 'stock request replay mismatch';
        END IF;
        SELECT COALESCE(SUM(CASE WHEN action='RESERVE' THEN quantity
                                 WHEN action IN ('ISSUE','RELEASE') THEN -quantity
                                 ELSE 0 END),0)::bigint AS outstanding
          INTO line FROM auto_work_order_stock_event
          WHERE work_order_id=p_order AND sku_id=p_sku;
        IF p_action IN ('ISSUE','RELEASE') AND line.outstanding < p_quantity THEN
          RAISE EXCEPTION 'work order reservation insufficient';
        END IF;
        SELECT on_hand,reserved INTO stock FROM auto_stock_balance
          WHERE sku_id=p_sku;
        IF p_action='RESERVE' AND stock.on_hand-stock.reserved < p_quantity THEN
          RAISE EXCEPTION 'stock unavailable';
        END IF;
        new_id := gen_random_uuid();
        INSERT INTO auto_stock_event
          (id,request_id,sku_id,organization_id,location_id,action,quantity)
          VALUES(new_id,p_request,p_sku,sku_row.organization_id,
                 sku_row.location_id,p_action,p_quantity);
        INSERT INTO auto_work_order_stock_event
          (stock_event_id,work_order_id,sku_id,action,quantity)
          VALUES(new_id,p_order,p_sku,p_action,p_quantity);
        RETURN new_id;
      END $fn$
    """)
