"""Append-only appointment booking replacement, no mutable appointment identity.

Revision ID: 0047_auto_reschedule
Revises: 0046_auto_appointment_actor
"""
from alembic import op
revision="0047_auto_reschedule"
down_revision="0046_auto_appointment_actor"
branch_labels=None
depends_on=None

def upgrade():
    op.execute("""
      CREATE TABLE auto_appointment_reschedule (
        id uuid PRIMARY KEY,
        appointment_id uuid NOT NULL REFERENCES auto_appointment(id),
        organization_id uuid NOT NULL REFERENCES auto_organization(id),
        old_booking_id uuid NOT NULL UNIQUE REFERENCES auto_bay_booking(id),
        new_booking_id uuid NOT NULL UNIQUE REFERENCES auto_bay_booking(id),
        actor_id uuid NOT NULL REFERENCES app_user(id),
        created_at timestamptz NOT NULL DEFAULT now(),
        CHECK (old_booking_id <> new_booking_id)
      )
    """)
    # A reschedule releases an old slot; it is not a business cancellation.
    # Preserve CREATED/CANCELED events only for genuine cancellations.
    op.execute("""
      CREATE OR REPLACE FUNCTION capture_auto_appointment_canceled()
      RETURNS trigger LANGUAGE plpgsql AS $fn$
      BEGIN
        IF OLD.canceled_at IS NULL AND NEW.canceled_at IS NOT NULL
           AND NOT EXISTS (SELECT 1 FROM auto_appointment_reschedule r
                           WHERE r.old_booking_id=NEW.id) THEN
          INSERT INTO auto_appointment_event(appointment_id,action)
          SELECT a.id,'BOOKING_CANCELED' FROM auto_appointment a
          WHERE a.booking_id=NEW.id;
        END IF;
        RETURN NEW;
      END $fn$
    """)
    op.execute("""
      CREATE OR REPLACE FUNCTION capture_auto_work_order_canceled()
      RETURNS trigger LANGUAGE plpgsql AS $fn$
      BEGIN
        IF OLD.canceled_at IS NULL AND NEW.canceled_at IS NOT NULL
           AND NOT EXISTS (SELECT 1 FROM auto_appointment_reschedule r
                           WHERE r.old_booking_id=NEW.id) THEN
          INSERT INTO auto_work_order_event(work_order_id,action)
          SELECT w.id,'APPOINTMENT_CANCELED'
          FROM auto_work_order w
          JOIN auto_appointment a ON a.id=w.appointment_id
          WHERE a.booking_id=NEW.id;
        END IF;
        RETURN NEW;
      END $fn$
    """)
    op.execute("""
      CREATE TRIGGER auto_appointment_reschedule_immutable
      BEFORE UPDATE OR DELETE ON auto_appointment_reschedule
      FOR EACH ROW EXECUTE FUNCTION reject_audit_mutation()
    """)

def downgrade():
    # Disposable synthetic database only. Never discard production audit data.
    op.execute("DROP TABLE auto_appointment_reschedule")
    op.execute("""
      CREATE OR REPLACE FUNCTION capture_auto_appointment_canceled()
      RETURNS trigger LANGUAGE plpgsql AS $fn$
      BEGIN
        IF OLD.canceled_at IS NULL AND NEW.canceled_at IS NOT NULL THEN
          INSERT INTO auto_appointment_event(appointment_id,action)
          SELECT a.id,'BOOKING_CANCELED' FROM auto_appointment a
          WHERE a.booking_id=NEW.id;
        END IF;
        RETURN NEW;
      END $fn$
    """)
    op.execute("""
      CREATE OR REPLACE FUNCTION capture_auto_work_order_canceled()
      RETURNS trigger LANGUAGE plpgsql AS $fn$
      BEGIN
        IF OLD.canceled_at IS NULL AND NEW.canceled_at IS NOT NULL THEN
          INSERT INTO auto_work_order_event(work_order_id,action)
          SELECT w.id,'APPOINTMENT_CANCELED'
          FROM auto_work_order w JOIN auto_appointment a ON a.id=w.appointment_id
          WHERE a.booking_id=NEW.id;
        END IF;
        RETURN NEW;
      END $fn$
    """)
