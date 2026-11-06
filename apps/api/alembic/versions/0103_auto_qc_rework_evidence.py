"""Immutable corrective-action evidence after QC FAIL, before independent QC PASS.

Revision ID: 0103_auto_qc_rework_evidence
Revises: 0102_auto_estimate_clarification
Synthetic only, no legal repair completion or release.
"""
from alembic import op

revision="0103_auto_qc_rework_evidence"
down_revision="0102_auto_estimate_clarification"
branch_labels=None
depends_on=None


def upgrade():
    op.execute("""CREATE TABLE auto_work_order_qc_rework (
      id uuid PRIMARY KEY,
      work_order_id uuid NOT NULL REFERENCES auto_work_order(id),
      fail_event_id uuid NOT NULL UNIQUE REFERENCES auto_work_order_qc_event(id),
      actor_id uuid NOT NULL REFERENCES app_user(id),
      action_code text NOT NULL CHECK (
        action_code IN ('ADJUSTMENT','DIAGNOSTIC_RECHECK','COMPONENT_RECHECK')
      ),
      request_key uuid NOT NULL UNIQUE,
      created_at timestamptz NOT NULL DEFAULT clock_timestamp()
    )""")
    op.execute("""CREATE INDEX auto_qc_rework_order_id_idx
      ON auto_work_order_qc_rework(work_order_id,created_at)""")
    op.execute("""CREATE FUNCTION guard_auto_qc_rework_insert()
      RETURNS trigger LANGUAGE plpgsql AS $f$
      DECLARE latest record;
      BEGIN
        SELECT id,result INTO latest FROM auto_work_order_qc_event
        WHERE work_order_id=NEW.work_order_id
        ORDER BY created_at DESC,id DESC LIMIT 1;
        IF NOT FOUND OR latest.result<>'FAIL' OR latest.id<>NEW.fail_event_id
          OR NOT EXISTS (
            SELECT 1 FROM auto_work_order_inspection i
            JOIN auto_work_order w ON w.id=i.work_order_id
            JOIN auto_appointment a ON a.id=w.appointment_id
            JOIN auto_vehicle_customer_link proof ON proof.id=a.customer_link_id
              AND proof.vehicle_id=a.vehicle_id
              AND proof.verified_at IS NOT NULL AND proof.revoked_at IS NULL
            WHERE i.work_order_id=NEW.work_order_id
              AND w.organization_id=a.organization_id
              AND w.location_id=a.location_id
          )
        THEN RAISE EXCEPTION 'rework requires current failed QC and live proof'; END IF;
        RETURN NEW;
      END $f$""")
    op.execute("""CREATE TRIGGER auto_qc_rework_insert_guard BEFORE INSERT
      ON auto_work_order_qc_rework FOR EACH ROW
      EXECUTE FUNCTION guard_auto_qc_rework_insert()""")
    op.execute("""CREATE TRIGGER auto_qc_rework_immutable
      BEFORE UPDATE OR DELETE ON auto_work_order_qc_rework
      FOR EACH ROW EXECUTE FUNCTION immutable_auto_stock_event()""")
    op.execute("""CREATE FUNCTION guard_auto_qc_pass_after_fail()
      RETURNS trigger LANGUAGE plpgsql AS $f$
      DECLARE previous record;
      BEGIN
        IF NEW.result<>'PASS' THEN RETURN NEW; END IF;
        SELECT id,result INTO previous FROM auto_work_order_qc_event
          WHERE work_order_id=NEW.work_order_id
          ORDER BY created_at DESC,id DESC LIMIT 1;
        IF FOUND AND previous.result='FAIL' AND NOT EXISTS(
          SELECT 1 FROM auto_work_order_qc_rework r
          WHERE r.fail_event_id=previous.id AND r.work_order_id=NEW.work_order_id
            AND r.actor_id<>NEW.actor_id
        ) THEN
          RAISE EXCEPTION 'independent rework evidence required before QC PASS';
        END IF;
        RETURN NEW;
      END $f$""")
    op.execute("""CREATE TRIGGER auto_qc_pass_after_fail_guard
      BEFORE INSERT ON auto_work_order_qc_event
      FOR EACH ROW EXECUTE FUNCTION guard_auto_qc_pass_after_fail()""")
    op.execute("REVOKE ALL ON auto_work_order_qc_rework FROM PUBLIC")


def downgrade():
    # Disposable synthetic PG17 only, never real immutable QC evidence.
    op.execute("DROP TRIGGER auto_qc_pass_after_fail_guard ON auto_work_order_qc_event")
    op.execute("DROP FUNCTION guard_auto_qc_pass_after_fail()")
    op.execute("DROP TABLE auto_work_order_qc_rework")
    op.execute("DROP FUNCTION guard_auto_qc_rework_insert()")
