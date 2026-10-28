"""Synthetic automotive work order bound one-to-one to an appointment.

Revision ID: 0044_auto_work_order
Revises: 0043_auto_appointment_events
"""
from alembic import op

revision = "0044_auto_work_order"
down_revision = "0043_auto_appointment_events"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
      CREATE TABLE auto_work_order (
        id uuid PRIMARY KEY,
        appointment_id uuid NOT NULL UNIQUE REFERENCES auto_appointment(id),
        organization_id uuid NOT NULL REFERENCES auto_organization(id),
        location_id uuid NOT NULL REFERENCES auto_service_location(id),
        created_at timestamptz NOT NULL DEFAULT now()
      )
    """)
    op.execute("""
      CREATE FUNCTION guard_auto_work_order()
      RETURNS trigger LANGUAGE plpgsql AS $fn$
      BEGIN
        IF TG_OP <> 'INSERT' THEN
          RAISE EXCEPTION 'work order identity immutable';
        END IF;
        IF NOT EXISTS (
          SELECT 1 FROM auto_appointment a
          JOIN auto_bay_booking b ON b.id=a.booking_id
          WHERE a.id=NEW.appointment_id
            AND a.organization_id=NEW.organization_id
            AND a.location_id=NEW.location_id
            AND b.canceled_at IS NULL
          FOR SHARE OF a,b
        ) THEN
          RAISE EXCEPTION 'appointment unavailable for work order';
        END IF;
        RETURN NEW;
      END $fn$
    """)
    op.execute("""
      CREATE TRIGGER auto_work_order_guard BEFORE INSERT OR UPDATE OR DELETE
      ON auto_work_order FOR EACH ROW EXECUTE FUNCTION guard_auto_work_order()
    """)


def downgrade():
    # Disposable synthetic databases only; drops work order identity.
    op.execute("DROP TABLE auto_work_order")
    op.execute("DROP FUNCTION guard_auto_work_order()")
