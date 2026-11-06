"""Immutable structured diagnostic findings bound to exact customer quote versions.

Revision ID: 0104_auto_diagnostic_evidence
Revises: 0103_auto_qc_rework_evidence
Synthetic-only diagnostic records. Not a signed act or medical/technical certification.
"""
from alembic import op

revision="0104_auto_diagnostic_evidence"
down_revision="0103_auto_qc_rework_evidence"
branch_labels=None
depends_on=None


def upgrade():
    op.execute("""CREATE TABLE auto_work_order_diagnostic (
      id uuid PRIMARY KEY,
      work_order_id uuid NOT NULL REFERENCES auto_work_order(id),
      service_id uuid NOT NULL REFERENCES auto_service_catalog(id),
      actor_id uuid NOT NULL REFERENCES app_user(id),
      finding_code text NOT NULL CHECK (finding_code IN
        ('NO_FAULT','MAINTENANCE_DUE','FAULT_CONFIRMED','FURTHER_CHECK')),
      severity_code text NOT NULL CHECK (severity_code IN ('ROUTINE','SOON','URGENT')),
      request_key uuid NOT NULL UNIQUE,
      created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
      UNIQUE (work_order_id,service_id),
      CHECK (finding_code<>'NO_FAULT' OR severity_code='ROUTINE')
    )""")
    op.execute("""CREATE INDEX auto_diagnostic_order_idx
      ON auto_work_order_diagnostic(work_order_id,service_id)""")
    op.execute("""CREATE FUNCTION guard_auto_work_order_diagnostic()
      RETURNS trigger LANGUAGE plpgsql AS $f$
      BEGIN
        IF NOT EXISTS(
          SELECT 1 FROM auto_work_order w
          JOIN auto_appointment a ON a.id=w.appointment_id
            AND a.organization_id=w.organization_id AND a.location_id=w.location_id
          JOIN LATERAL (
            SELECT COALESCE((SELECT r.new_booking_id
              FROM auto_appointment_reschedule r WHERE r.appointment_id=a.id
              ORDER BY r.created_at DESC,r.id DESC LIMIT 1),a.booking_id) AS current_id
          ) current_booking ON TRUE
          JOIN auto_bay_booking b ON b.id=current_booking.current_id
            AND b.organization_id=w.organization_id
            AND b.location_id=w.location_id AND b.canceled_at IS NULL
          JOIN auto_vehicle_customer_link proof ON proof.id=a.customer_link_id
            AND proof.vehicle_id=a.vehicle_id
            AND proof.verified_at IS NOT NULL AND proof.revoked_at IS NULL
          JOIN auto_vehicle_location_binding binding ON binding.vehicle_id=a.vehicle_id
            AND binding.location_id=w.location_id
          JOIN auto_organization org ON org.id=w.organization_id AND org.disabled_at IS NULL
          JOIN auto_service_location loc ON loc.id=w.location_id
            AND loc.organization_id=w.organization_id AND loc.disabled_at IS NULL
          JOIN auto_work_order_inspection inspection ON inspection.work_order_id=w.id
          JOIN auto_work_order_service chosen ON chosen.work_order_id=w.id
            AND chosen.service_id=NEW.service_id
            AND chosen.organization_id=w.organization_id
            AND chosen.location_id=w.location_id
          JOIN auto_organization_role_grant g ON g.organization_id=w.organization_id
            AND g.user_id=NEW.actor_id AND g.role_code='MECHANIC'
            AND g.revoked_at IS NULL
            AND (g.location_id=w.location_id OR g.location_id IS NULL)
          WHERE w.id=NEW.work_order_id
            AND NOT EXISTS(SELECT 1 FROM auto_work_order_estimate_revision r
              WHERE r.work_order_id=w.id)
          FOR SHARE OF w,a,b,proof,binding,org,loc,inspection,chosen,g
        ) THEN RAISE EXCEPTION 'diagnostic requires inspection, selected service, live mechanic and no quote'; END IF;
        RETURN NEW;
      END $f$""")
    op.execute("""CREATE TRIGGER auto_work_order_diagnostic_guard
      BEFORE INSERT ON auto_work_order_diagnostic
      FOR EACH ROW EXECUTE FUNCTION guard_auto_work_order_diagnostic()""")
    op.execute("""CREATE TRIGGER auto_work_order_diagnostic_immutable
      BEFORE UPDATE OR DELETE ON auto_work_order_diagnostic
      FOR EACH ROW EXECUTE FUNCTION immutable_auto_stock_event()""")
    op.execute("REVOKE ALL ON auto_work_order_diagnostic FROM PUBLIC")
    op.execute("""ALTER TABLE auto_work_order_estimate_revision
      ADD COLUMN diagnostics_snapshot jsonb NOT NULL DEFAULT '[]'::jsonb
      CHECK(jsonb_typeof(diagnostics_snapshot)='array'
        AND jsonb_array_length(diagnostics_snapshot)<=50)""")
    op.execute("""CREATE FUNCTION guard_auto_estimate_diagnostic_snapshot()
      RETURNS trigger LANGUAGE plpgsql AS $f$
      DECLARE expected jsonb;
      BEGIN
        SELECT COALESCE(jsonb_agg(jsonb_build_object(
          'serviceId',d.service_id::text,
          'findingCode',d.finding_code,'severityCode',d.severity_code
        ) ORDER BY s.code,d.service_id),'[]'::jsonb)
        INTO expected FROM auto_work_order_diagnostic d
        JOIN auto_service_catalog s ON s.id=d.service_id
        WHERE d.work_order_id=NEW.work_order_id;
        IF NEW.diagnostics_snapshot IS DISTINCT FROM expected THEN
          RAISE EXCEPTION 'estimate diagnostic snapshot differs from immutable findings';
        END IF;
        RETURN NEW;
      END $f$""")
    op.execute("""CREATE TRIGGER auto_estimate_diagnostic_snapshot_guard
      BEFORE INSERT ON auto_work_order_estimate_revision
      FOR EACH ROW EXECUTE FUNCTION guard_auto_estimate_diagnostic_snapshot()""")


def downgrade():
    # Disposable synthetic DB only. Never delete actual diagnostic / customer evidence.
    op.execute("DROP TRIGGER auto_estimate_diagnostic_snapshot_guard ON auto_work_order_estimate_revision")
    op.execute("DROP FUNCTION guard_auto_estimate_diagnostic_snapshot()")
    op.execute("ALTER TABLE auto_work_order_estimate_revision DROP COLUMN diagnostics_snapshot")
    op.execute("DROP TABLE auto_work_order_diagnostic")
    op.execute("DROP FUNCTION guard_auto_work_order_diagnostic()")
