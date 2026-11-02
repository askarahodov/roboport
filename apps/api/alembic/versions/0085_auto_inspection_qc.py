"""Automotive synthetic intake inspection and independent QC audit.

Revision ID: 0085_auto_inspection_qc
Revises: 0084_auto_order_services
"""
from alembic import op

revision = "0085_auto_inspection_qc"
down_revision = "0084_auto_order_services"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
      CREATE TABLE auto_work_order_inspection (
        id uuid PRIMARY KEY,
        work_order_id uuid NOT NULL UNIQUE REFERENCES auto_work_order(id),
        organization_id uuid NOT NULL REFERENCES auto_organization(id),
        location_id uuid NOT NULL REFERENCES auto_service_location(id),
        actor_id uuid NOT NULL REFERENCES app_user(id),
        odometer_km integer NOT NULL CHECK (odometer_km BETWEEN 0 AND 2000000),
        fuel_percent integer NOT NULL CHECK (fuel_percent BETWEEN 0 AND 100),
        tyres_ok boolean NOT NULL,
        lights_ok boolean NOT NULL,
        fluid_leak boolean NOT NULL,
        exterior_code text NOT NULL CHECK (exterior_code IN ('CLEAR','DAMAGE','UNCHECKED')),
        created_at timestamptz NOT NULL DEFAULT clock_timestamp()
      )
    """)
    op.execute("""
      CREATE TABLE auto_work_order_qc_event (
        id uuid PRIMARY KEY,
        work_order_id uuid NOT NULL REFERENCES auto_work_order(id),
        inspection_id uuid NOT NULL REFERENCES auto_work_order_inspection(id),
        organization_id uuid NOT NULL REFERENCES auto_organization(id),
        location_id uuid NOT NULL REFERENCES auto_service_location(id),
        actor_id uuid NOT NULL REFERENCES app_user(id),
        request_key uuid NOT NULL,
        result text NOT NULL CHECK (result IN ('PASS','FAIL')),
        finding_code text NOT NULL CHECK (
          (result='PASS' AND finding_code='NONE')
          OR (result='FAIL' AND finding_code IN ('DEFECT','REWORK'))
        ),
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        UNIQUE (work_order_id, request_key)
      )
    """)
    op.execute("""
      CREATE FUNCTION guard_auto_inspection_work_order()
      RETURNS trigger LANGUAGE plpgsql AS $fn$
      BEGIN
        IF NOT EXISTS (
          SELECT 1 FROM auto_work_order w
          JOIN auto_appointment a ON a.id=w.appointment_id
          JOIN LATERAL (
            SELECT COALESCE((
              SELECT r.new_booking_id FROM auto_appointment_reschedule r
              WHERE r.appointment_id=a.id
              ORDER BY r.created_at DESC,r.id DESC LIMIT 1
            ),a.booking_id) AS current_id
          ) current_booking ON TRUE
          JOIN auto_bay_booking b ON b.id=current_booking.current_id
          JOIN auto_vehicle_customer_link proof
            ON proof.id=a.customer_link_id AND proof.vehicle_id=a.vehicle_id
          JOIN auto_vehicle_location_binding binding
            ON binding.vehicle_id=a.vehicle_id AND binding.location_id=w.location_id
          JOIN auto_organization o ON o.id=w.organization_id
          JOIN auto_service_location loc ON loc.id=w.location_id
            AND loc.organization_id=o.id
          WHERE w.id=NEW.work_order_id
            AND w.organization_id=NEW.organization_id
            AND w.location_id=NEW.location_id
            AND a.organization_id=w.organization_id
            AND a.location_id=w.location_id
            AND b.organization_id=w.organization_id
            AND b.location_id=w.location_id
            AND b.canceled_at IS NULL
            AND proof.verified_at IS NOT NULL AND proof.revoked_at IS NULL
            AND o.disabled_at IS NULL AND loc.disabled_at IS NULL
          FOR SHARE OF w,a,b,proof,binding,o,loc
        ) THEN RAISE EXCEPTION 'inspection scope or proof unavailable'; END IF;
        IF TG_TABLE_NAME='auto_work_order_qc_event' THEN
          IF NOT EXISTS (
            SELECT 1 FROM auto_work_order_inspection i
            WHERE i.id=NEW.inspection_id
              AND i.work_order_id=NEW.work_order_id
              AND i.organization_id=NEW.organization_id
              AND i.location_id=NEW.location_id
              AND i.actor_id<>NEW.actor_id
            FOR SHARE
          ) THEN RAISE EXCEPTION 'inspection or independent actor unavailable'; END IF;
        END IF;
        RETURN NEW;
      END $fn$
    """)
    for table in ("auto_work_order_inspection", "auto_work_order_qc_event"):
        op.execute(f"""CREATE TRIGGER {table}_scope BEFORE INSERT ON {table}
                     FOR EACH ROW EXECUTE FUNCTION guard_auto_inspection_work_order()""")
        op.execute(f"""CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE
                     ON {table} FOR EACH ROW EXECUTE FUNCTION immutable_auto_stock_event()""")
        op.execute(f"REVOKE ALL ON {table} FROM PUBLIC")


def downgrade():
    # Disposable synthetic database only. Never delete actual inspection/QC audit.
    op.execute("DROP TABLE auto_work_order_qc_event")
    op.execute("DROP TABLE auto_work_order_inspection")
    op.execute("DROP FUNCTION guard_auto_inspection_work_order()")
