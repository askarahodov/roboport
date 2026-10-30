"""Synthetic WorkOrder financial movements, no payments or fiscal receipts.

Revision ID: 0073_auto_order_financial
Revises: 0072_auto_supplier_return
"""
from alembic import op

revision = "0073_auto_order_financial"
down_revision = "0072_auto_supplier_return"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
      CREATE TABLE auto_work_order_financial_event (
        id uuid PRIMARY KEY,
        request_id uuid NOT NULL UNIQUE,
        work_order_id uuid NOT NULL REFERENCES auto_work_order(id),
        organization_id uuid NOT NULL REFERENCES auto_organization(id),
        location_id uuid NOT NULL REFERENCES auto_service_location(id),
        action text NOT NULL CHECK (action IN ('CHARGE','CREDIT')),
        amount_minor bigint NOT NULL CHECK (amount_minor BETWEEN 1 AND 1000000000000),
        reason text NOT NULL CHECK (length(btrim(reason)) BETWEEN 3 AND 160),
        created_at timestamptz NOT NULL DEFAULT now()
      )
    """)
    op.execute("""CREATE INDEX ix_auto_financial_order
       ON auto_work_order_financial_event(work_order_id,created_at,id)""")
    op.execute("""CREATE FUNCTION guard_auto_work_order_financial()
       RETURNS trigger LANGUAGE plpgsql AS $fn$
       DECLARE order_row record;
       BEGIN
         IF TG_OP <> 'INSERT' THEN
           RAISE EXCEPTION 'financial history immutable';
         END IF;
         SELECT w.organization_id,w.location_id,b.canceled_at
           INTO order_row FROM auto_work_order w
           JOIN auto_appointment a ON a.id=w.appointment_id
           JOIN auto_bay_booking b ON b.id=a.booking_id
           JOIN auto_organization o ON o.id=w.organization_id
           JOIN auto_service_location l ON l.id=w.location_id
           WHERE w.id=NEW.work_order_id AND o.disabled_at IS NULL
             AND l.disabled_at IS NULL FOR SHARE OF w,a,b,o,l;
         IF NOT FOUND OR order_row.canceled_at IS NOT NULL
            OR (NEW.organization_id,NEW.location_id)
               IS DISTINCT FROM (order_row.organization_id,order_row.location_id)
         THEN RAISE EXCEPTION 'financial work order scope unavailable'; END IF;
         RETURN NEW;
       END $fn$""")
    op.execute("""CREATE TRIGGER auto_work_order_financial_guard
       BEFORE INSERT OR UPDATE OR DELETE ON auto_work_order_financial_event
       FOR EACH ROW EXECUTE FUNCTION guard_auto_work_order_financial()""")
    op.execute("""CREATE FUNCTION record_auto_work_order_financial(
      p_order uuid,p_request uuid,p_action text,p_amount bigint,p_reason text
    ) RETURNS uuid LANGUAGE plpgsql AS $fn$
    DECLARE identity record; existing record; new_id uuid;
    BEGIN
      IF p_order IS NULL OR p_request IS NULL OR p_action IS NULL
        OR p_action NOT IN ('CHARGE','CREDIT')
        OR p_amount IS NULL OR p_amount < 1 OR p_amount > 1000000000000
        OR p_reason IS NULL OR length(btrim(p_reason)) NOT BETWEEN 3 AND 160
      THEN RAISE EXCEPTION 'invalid financial entry'; END IF;
      SELECT id,work_order_id,action,amount_minor,reason INTO existing
        FROM auto_work_order_financial_event WHERE request_id=p_request;
      IF FOUND THEN
        IF (existing.work_order_id,existing.action,existing.amount_minor,existing.reason)
           IS DISTINCT FROM (p_order,p_action,p_amount,p_reason)
        THEN RAISE EXCEPTION 'financial idempotency payload mismatch'; END IF;
        RETURN existing.id;
      END IF;
      SELECT organization_id,location_id INTO identity
        FROM auto_work_order WHERE id=p_order FOR SHARE;
      IF NOT FOUND THEN RAISE EXCEPTION 'work order unavailable'; END IF;
      new_id:=gen_random_uuid();
      INSERT INTO auto_work_order_financial_event
        (id,request_id,work_order_id,organization_id,location_id,action,amount_minor,reason)
      VALUES(new_id,p_request,p_order,identity.organization_id,identity.location_id,
             p_action,p_amount,p_reason);
      RETURN new_id;
    END $fn$""")
    op.execute("""CREATE VIEW auto_work_order_financial_balance AS
       SELECT w.id AS work_order_id,w.organization_id,w.location_id,
         COALESCE(SUM(CASE WHEN e.action='CHARGE' THEN e.amount_minor
                           WHEN e.action='CREDIT' THEN -e.amount_minor
                           ELSE 0 END),0)::bigint AS balance_minor
       FROM auto_work_order w LEFT JOIN auto_work_order_financial_event e
         ON e.work_order_id=w.id
       GROUP BY w.id,w.organization_id,w.location_id""")


def downgrade():
    op.execute("DROP VIEW auto_work_order_financial_balance")
    op.execute("DROP FUNCTION record_auto_work_order_financial(uuid,uuid,text,bigint,text)")
    op.execute("DROP TRIGGER auto_work_order_financial_guard ON auto_work_order_financial_event")
    op.execute("DROP FUNCTION guard_auto_work_order_financial()")
    op.execute("DROP TABLE auto_work_order_financial_event")
