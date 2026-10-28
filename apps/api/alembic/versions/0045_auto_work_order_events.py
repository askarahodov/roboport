"""Append-only WorkOrder lifecycle receipts, synchronized with booking cancellation.

Revision ID: 0045_auto_work_order_events
Revises: 0044_auto_work_order
"""
from alembic import op

revision = "0045_auto_work_order_events"
down_revision = "0044_auto_work_order"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
      CREATE TABLE auto_work_order_event (
        id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        work_order_id uuid NOT NULL REFERENCES auto_work_order(id),
        action text NOT NULL CHECK (action IN ('CREATED','APPOINTMENT_CANCELED')),
        recorded_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE(work_order_id,action)
      )
    """)
    op.execute("""
      CREATE TRIGGER auto_work_order_event_immutable BEFORE UPDATE OR DELETE
      ON auto_work_order_event FOR EACH ROW EXECUTE FUNCTION reject_audit_mutation()
    """)
    op.execute("""
      CREATE FUNCTION capture_auto_work_order_created() RETURNS trigger
      LANGUAGE plpgsql AS $fn$
      BEGIN
        INSERT INTO auto_work_order_event(work_order_id,action)
        VALUES (NEW.id,'CREATED');
        RETURN NEW;
      END $fn$
    """)
    op.execute("""
      CREATE FUNCTION capture_auto_work_order_canceled() RETURNS trigger
      LANGUAGE plpgsql AS $fn$
      BEGIN
        IF OLD.canceled_at IS NULL AND NEW.canceled_at IS NOT NULL THEN
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
      CREATE TRIGGER auto_work_order_created_event AFTER INSERT
      ON auto_work_order FOR EACH ROW EXECUTE FUNCTION capture_auto_work_order_created()
    """)
    op.execute("""
      CREATE TRIGGER auto_work_order_canceled_event AFTER UPDATE OF canceled_at
      ON auto_bay_booking FOR EACH ROW EXECUTE FUNCTION capture_auto_work_order_canceled()
    """)


def downgrade():
    # Disposable synthetic databases only; removes historical evidence.
    op.execute("DROP TRIGGER auto_work_order_canceled_event ON auto_bay_booking")
    op.execute("DROP TRIGGER auto_work_order_created_event ON auto_work_order")
    op.execute("DROP TABLE auto_work_order_event")
    op.execute("DROP FUNCTION capture_auto_work_order_canceled()")
    op.execute("DROP FUNCTION capture_auto_work_order_created()")
