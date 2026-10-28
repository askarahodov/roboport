"""Append-only synthetic appointment lifecycle generated from booking transitions.

Revision ID: 0043_auto_appointment_events
Revises: 0042_auto_appointment
"""
from alembic import op

revision = "0043_auto_appointment_events"
down_revision = "0042_auto_appointment"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
      CREATE TABLE auto_appointment_event (
        id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        appointment_id uuid NOT NULL REFERENCES auto_appointment(id),
        action text NOT NULL CHECK (action IN ('CREATED','BOOKING_CANCELED')),
        recorded_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE (appointment_id,action)
      )
    """)
    op.execute("""
      CREATE TRIGGER auto_appointment_event_immutable BEFORE UPDATE OR DELETE
      ON auto_appointment_event FOR EACH ROW EXECUTE FUNCTION reject_audit_mutation()
    """)
    op.execute("""
      CREATE FUNCTION capture_auto_appointment_created()
      RETURNS trigger LANGUAGE plpgsql AS $fn$
      BEGIN
        INSERT INTO auto_appointment_event(appointment_id,action)
        VALUES (NEW.id,'CREATED');
        RETURN NEW;
      END $fn$
    """)
    op.execute("""
      CREATE FUNCTION capture_auto_appointment_canceled()
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
      CREATE TRIGGER auto_appointment_created_event AFTER INSERT
      ON auto_appointment FOR EACH ROW EXECUTE FUNCTION capture_auto_appointment_created()
    """)
    op.execute("""
      CREATE TRIGGER auto_appointment_canceled_event AFTER UPDATE OF canceled_at
      ON auto_bay_booking FOR EACH ROW EXECUTE FUNCTION capture_auto_appointment_canceled()
    """)


def downgrade():
    # Synthetic disposable DB only; loses all lifecycle event history.
    op.execute("DROP TRIGGER auto_appointment_canceled_event ON auto_bay_booking")
    op.execute("DROP TRIGGER auto_appointment_created_event ON auto_appointment")
    op.execute("DROP TABLE auto_appointment_event")
    op.execute("DROP FUNCTION capture_auto_appointment_canceled()")
    op.execute("DROP FUNCTION capture_auto_appointment_created()")
