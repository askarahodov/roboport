"""Disallow mechanic acknowledgement after full return of issued parts.

Revision ID: 0067_auto_handoff_return_guard
Revises: 0066_auto_mechanic_handoff
"""
from alembic import op

revision="0067_auto_handoff_return_guard"
down_revision="0066_auto_mechanic_handoff"
branch_labels=None
depends_on=None


def upgrade():
    op.execute("""CREATE OR REPLACE FUNCTION record_auto_stock_mechanic_handoff(
      p_issue uuid,p_request uuid
    ) RETURNS uuid LANGUAGE plpgsql AS $fn$
      DECLARE prior record; issue_row record; result_id uuid; outstanding bigint;
      BEGIN
        IF p_issue IS NULL OR p_request IS NULL THEN
          RAISE EXCEPTION 'invalid handoff'; END IF;
        SELECT i.id,i.sku_id,i.work_order_id,i.lot_id INTO issue_row
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
        SELECT COALESCE(SUM(q),0)::bigint INTO outstanding FROM (
          SELECT quantity AS q FROM auto_stock_lot_work_order_issue
            WHERE work_order_id=issue_row.work_order_id AND lot_id=issue_row.lot_id
          UNION ALL
          SELECT -quantity AS q FROM auto_stock_lot_work_order_return
            WHERE work_order_id=issue_row.work_order_id AND lot_id=issue_row.lot_id
        ) movements;
        IF outstanding<=0 THEN
          RAISE EXCEPTION 'no parts outstanding for mechanic handoff'; END IF;
        result_id:=gen_random_uuid();
        INSERT INTO auto_stock_mechanic_handoff(id,request_id,issue_id,received_by)
          VALUES(result_id,p_request,p_issue,
            NULLIF(current_setting('roboport.mechanic_handoff_actor',true),'')::uuid);
        RETURN result_id;
      END $fn$""")


def downgrade():
    # Synthetic database only; rollback restores original receipt contract.
    op.execute("""CREATE OR REPLACE FUNCTION record_auto_stock_mechanic_handoff(
      p_issue uuid,p_request uuid
    ) RETURNS uuid LANGUAGE plpgsql AS $fn$
      DECLARE prior record; issue_row record; result_id uuid;
      BEGIN
        IF p_issue IS NULL OR p_request IS NULL THEN RAISE EXCEPTION 'invalid handoff'; END IF;
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
          IF prior.issue_id=p_issue AND prior.received_by=
            NULLIF(current_setting('roboport.mechanic_handoff_actor',true),'')::uuid
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
