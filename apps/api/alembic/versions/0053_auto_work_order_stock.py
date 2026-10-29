"""Work-order bound reservation and consumption ledger (synthetic PG17).

Revision ID: 0053_auto_work_order_stock
Revises: 0052_auto_stock_ledger
"""
from alembic import op

revision = "0053_auto_work_order_stock"
down_revision = "0052_auto_stock_ledger"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
      ALTER TABLE auto_stock_event DROP CONSTRAINT auto_stock_event_action_check
    """)
    op.execute("""
      ALTER TABLE auto_stock_event ADD CONSTRAINT auto_stock_event_action_check
      CHECK (action IN ('RECEIPT','RESERVE','RELEASE','ISSUE'))
    """)
    op.execute("""
      CREATE OR REPLACE VIEW auto_stock_balance AS
      SELECT sku.id AS sku_id, sku.organization_id, sku.location_id,
             COALESCE(SUM(CASE WHEN e.action='RECEIPT' THEN e.quantity
                               WHEN e.action='ISSUE' THEN -e.quantity ELSE 0 END),0)::bigint AS on_hand,
             COALESCE(SUM(CASE WHEN e.action='RESERVE' THEN e.quantity
                               WHEN e.action IN ('RELEASE','ISSUE') THEN -e.quantity
                               ELSE 0 END),0)::bigint AS reserved
        FROM auto_stock_sku sku LEFT JOIN auto_stock_event e ON e.sku_id=sku.id
       GROUP BY sku.id,sku.organization_id,sku.location_id
    """)
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
        SELECT on_hand,reserved INTO stock
          FROM auto_stock_balance WHERE sku_id=NEW.sku_id;
        IF (NEW.action='RESERVE' AND stock.on_hand-stock.reserved < NEW.quantity)
           OR (NEW.action IN ('RELEASE','ISSUE') AND stock.reserved < NEW.quantity) THEN
          RAISE EXCEPTION 'insufficient stock or reservation';
        END IF;
        RETURN NEW;
      END $fn$
    """)
    op.execute("""
      CREATE TABLE auto_work_order_stock_event (
        stock_event_id uuid PRIMARY KEY REFERENCES auto_stock_event(id),
        work_order_id uuid NOT NULL REFERENCES auto_work_order(id),
        sku_id uuid NOT NULL REFERENCES auto_stock_sku(id),
        action text NOT NULL CHECK (action IN ('RESERVE','RELEASE','ISSUE')),
        quantity bigint NOT NULL CHECK (quantity BETWEEN 1 AND 1000000000),
        created_at timestamptz NOT NULL DEFAULT now()
      )
    """)
    op.execute("""
      CREATE INDEX ix_auto_work_order_stock_event_scope
        ON auto_work_order_stock_event(work_order_id,sku_id)
    """)
    op.execute("""
      CREATE TRIGGER auto_work_order_stock_event_immutable
      BEFORE UPDATE OR DELETE ON auto_work_order_stock_event
      FOR EACH ROW EXECUTE FUNCTION immutable_auto_stock_event()
    """)
    op.execute("""
      CREATE FUNCTION record_auto_work_order_stock(
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
    op.execute("REVOKE ALL ON FUNCTION record_auto_work_order_stock(uuid,uuid,uuid,text,bigint) FROM PUBLIC")


def downgrade():
    # Disposable synthetic data only: destroys work-order stock events.
    op.execute("DROP FUNCTION record_auto_work_order_stock(uuid,uuid,uuid,text,bigint)")
    op.execute("DROP TABLE auto_work_order_stock_event")
    op.execute("""
      CREATE OR REPLACE FUNCTION guard_auto_stock_event_insert()
      RETURNS trigger LANGUAGE plpgsql AS $fn$
      DECLARE scope record; stock record;
      BEGIN
        SELECT organization_id,location_id INTO scope FROM auto_stock_sku
          WHERE id=NEW.sku_id FOR UPDATE;
        IF NOT FOUND OR (NEW.organization_id,NEW.location_id)
            IS DISTINCT FROM (scope.organization_id,scope.location_id) THEN
          RAISE EXCEPTION 'foreign stock scope';
        END IF;
        SELECT on_hand,reserved INTO stock FROM auto_stock_balance
          WHERE sku_id=NEW.sku_id;
        IF (NEW.action='RESERVE' AND stock.on_hand-stock.reserved < NEW.quantity)
           OR (NEW.action='RELEASE' AND stock.reserved < NEW.quantity) THEN
          RAISE EXCEPTION 'insufficient stock or reservation';
        END IF;
        RETURN NEW;
      END $fn$
    """)
    op.execute("""
      CREATE OR REPLACE VIEW auto_stock_balance AS
      SELECT sku.id AS sku_id,sku.organization_id,sku.location_id,
             COALESCE(SUM(CASE WHEN e.action='RECEIPT' THEN e.quantity ELSE 0 END),0)::bigint AS on_hand,
             COALESCE(SUM(CASE WHEN e.action='RESERVE' THEN e.quantity
                               WHEN e.action='RELEASE' THEN -e.quantity
                               ELSE 0 END),0)::bigint AS reserved
        FROM auto_stock_sku sku LEFT JOIN auto_stock_event e ON e.sku_id=sku.id
       GROUP BY sku.id,sku.organization_id,sku.location_id
    """)
    op.execute("ALTER TABLE auto_stock_event DROP CONSTRAINT auto_stock_event_action_check")
    op.execute("""
      ALTER TABLE auto_stock_event ADD CONSTRAINT auto_stock_event_action_check
      CHECK (action IN ('RECEIPT','RESERVE','RELEASE'))
    """)
