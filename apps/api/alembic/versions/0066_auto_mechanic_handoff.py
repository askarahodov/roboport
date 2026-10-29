"""Immutable mechanic confirmation of an existing lot work-order issue.

Revision ID: 0066_auto_mechanic_handoff
Revises: 0065_auto_receipt_valuation
"""
from alembic import op

revision="0066_auto_mechanic_handoff"
down_revision="0065_auto_receipt_valuation"
branch_labels=None
depends_on=None


def upgrade():
    op.execute("""CREATE TABLE auto_stock_mechanic_handoff(
      id uuid PRIMARY KEY,
      request_id uuid NOT NULL UNIQUE,
      issue_id uuid NOT NULL UNIQUE REFERENCES auto_stock_lot_work_order_issue(id),
      received_by uuid NOT NULL REFERENCES app_user(id),
      recorded_at timestamptz NOT NULL DEFAULT now()
    )""")
    op.execute("""CREATE TRIGGER auto_stock_mechanic_handoff_immutable
      BEFORE UPDATE OR DELETE ON auto_stock_mechanic_handoff
      FOR EACH ROW EXECUTE FUNCTION immutable_auto_stock_event()""")
    op.execute("""CREATE FUNCTION guard_auto_stock_mechanic_handoff()
      RETURNS trigger LANGUAGE plpgsql AS $fn$
      DECLARE actor uuid;
      BEGIN
        actor:=NULLIF(current_setting('roboport.mechanic_handoff_actor',true),'')::uuid;
        IF actor IS NULL THEN RAISE EXCEPTION 'handoff actor required'; END IF;
        IF NOT EXISTS(SELECT 1 FROM auto_stock_lot_work_order_issue
          WHERE id=NEW.issue_id) THEN RAISE EXCEPTION 'issue required'; END IF;
        NEW.received_by:=actor;
        RETURN NEW;
      END $fn$""")
    op.execute("""CREATE TRIGGER auto_stock_mechanic_handoff_guard
      BEFORE INSERT ON auto_stock_mechanic_handoff
      FOR EACH ROW EXECUTE FUNCTION guard_auto_stock_mechanic_handoff()""")
    op.execute("""CREATE FUNCTION record_auto_stock_mechanic_handoff(
      p_issue uuid,p_request uuid
    ) RETURNS uuid LANGUAGE plpgsql AS $fn$
      DECLARE prior record; issue_row record; result_id uuid;
      BEGIN
        IF p_issue IS NULL OR p_request IS NULL THEN
          RAISE EXCEPTION 'invalid handoff'; END IF;
        SELECT i.id,i.sku_id INTO issue_row
          FROM auto_stock_lot_work_order_issue i
          JOIN auto_work_order w ON w.id=i.work_order_id
          JOIN auto_service_location l ON l.id=w.location_id
             AND l.organization_id=w.organization_id
          JOIN auto_organization o ON o.id=w.organization_id
          WHERE i.id=p_issue AND l.disabled_at IS NULL AND o.disabled_at IS NULL;
        IF NOT FOUND THEN RAISE EXCEPTION 'issue unavailable'; END IF;
        PERFORM 1 FROM auto_stock_sku WHERE id=issue_row.sku_id FOR UPDATE;
        SELECT * INTO prior FROM auto_stock_mechanic_handoff WHERE request_id=p_request;
        IF FOUND THEN
          IF prior.issue_id=p_issue AND
             prior.received_by=NULLIF(current_setting('roboport.mechanic_handoff_actor',true),'')::uuid
          THEN RETURN prior.id; END IF;
          RAISE EXCEPTION 'handoff replay mismatch'; END IF;
        IF EXISTS(SELECT 1 FROM auto_stock_mechanic_handoff WHERE issue_id=p_issue)
        THEN RAISE EXCEPTION 'issue already acknowledged'; END IF;
        result_id:=gen_random_uuid();
        INSERT INTO auto_stock_mechanic_handoff(id,request_id,issue_id,received_by)
          VALUES(result_id,p_request,p_issue,
            NULLIF(current_setting('roboport.mechanic_handoff_actor',true),'')::uuid);
        RETURN result_id;
      END $fn$""")
    op.execute("""REVOKE ALL ON FUNCTION
      record_auto_stock_mechanic_handoff(uuid,uuid) FROM PUBLIC""")


def downgrade():
    op.execute("DROP FUNCTION record_auto_stock_mechanic_handoff(uuid,uuid)")
    op.execute("DROP TABLE auto_stock_mechanic_handoff")
    op.execute("DROP FUNCTION guard_auto_stock_mechanic_handoff()")
