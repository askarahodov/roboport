"""Capture cancellation events for the current booking after reschedules.

Revision ID: 0048_auto_current_cancel
Revises: 0047_auto_reschedule
"""
from alembic import op
revision="0048_auto_current_cancel"
down_revision="0047_auto_reschedule"
branch_labels=None
depends_on=None

def _capture(include_replacements):
    extra = """ OR EXISTS (
          SELECT 1 FROM auto_appointment_reschedule r
          WHERE r.appointment_id=a.id AND r.new_booking_id=NEW.id
        )""" if include_replacements else ""
    op.execute(f"""
      CREATE OR REPLACE FUNCTION capture_auto_appointment_canceled()
      RETURNS trigger LANGUAGE plpgsql AS $fn$
      BEGIN
        IF OLD.canceled_at IS NULL AND NEW.canceled_at IS NOT NULL
           AND NOT EXISTS (SELECT 1 FROM auto_appointment_reschedule r
                           WHERE r.old_booking_id=NEW.id) THEN
          INSERT INTO auto_appointment_event(appointment_id,action)
          SELECT a.id,'BOOKING_CANCELED' FROM auto_appointment a
          WHERE a.booking_id=NEW.id {extra};
        END IF;
        RETURN NEW;
      END $fn$
    """)
    op.execute(f"""
      CREATE OR REPLACE FUNCTION capture_auto_work_order_canceled()
      RETURNS trigger LANGUAGE plpgsql AS $fn$
      BEGIN
        IF OLD.canceled_at IS NULL AND NEW.canceled_at IS NOT NULL
           AND NOT EXISTS (SELECT 1 FROM auto_appointment_reschedule r
                           WHERE r.old_booking_id=NEW.id) THEN
          INSERT INTO auto_work_order_event(work_order_id,action)
          SELECT w.id,'APPOINTMENT_CANCELED'
          FROM auto_work_order w JOIN auto_appointment a ON a.id=w.appointment_id
          WHERE a.booking_id=NEW.id {extra};
        END IF;
        RETURN NEW;
      END $fn$
    """)

def upgrade():
    _capture(True)

def downgrade():
    # Disposable synthetic DB only.
    _capture(False)
