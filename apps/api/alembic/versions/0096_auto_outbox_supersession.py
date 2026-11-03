"""Supersede stale synthetic automotive appointment notifications, preserving audit.

Revision ID: 0096_auto_outbox_supersession
Revises: 0095_auto_cancel_idempotency

This is a DEV_SINK-only state machine; no external delivery is enabled.
"""
from alembic import op
from sqlalchemy import text

revision = "0096_auto_outbox_supersession"
down_revision = "0095_auto_cancel_idempotency"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE notification_outbox DROP CONSTRAINT notification_outbox_status_check")
    op.execute("""ALTER TABLE notification_outbox ADD CONSTRAINT notification_outbox_status_check
      CHECK (status IN ('PENDING','PROCESSING','DELIVERED','DEAD','SUPERSEDED'))""")
    # A superseded receipt is terminal. Worker fencing must never revive it.
    op.execute("""
      CREATE FUNCTION guard_superseded_auto_outbox()
      RETURNS trigger LANGUAGE plpgsql AS $fn$
      BEGIN
        IF OLD.status='SUPERSEDED' THEN
          RAISE EXCEPTION 'superseded notification is immutable';
        END IF;
        RETURN NEW;
      END $fn$
    """)
    op.execute("""CREATE TRIGGER auto_outbox_superseded_immutable
      BEFORE UPDATE ON notification_outbox
      FOR EACH ROW EXECUTE FUNCTION guard_superseded_auto_outbox()""")

    # AFTER INSERT is in the same transaction as the original appointment
    # event trigger. Row locks serialize worker finish vs supersession.
    # Delivered receipts remain historical truth, never retroactively erased.
    op.execute("""
      CREATE FUNCTION supersede_stale_auto_appointment_notifications()
      RETURNS trigger LANGUAGE plpgsql AS $fn$
      BEGIN
        IF NEW.aggregate_type='auto_appointment'
           AND NEW.event_type IN ('RESCHEDULED','CANCELED') THEN
          UPDATE notification_outbox SET
            status='SUPERSEDED', claimed_by=NULL, claimed_at=NULL,
            last_error='superseded by newer appointment state'
          WHERE aggregate_type='auto_appointment'
            AND aggregate_id=NEW.aggregate_id
            AND id<>NEW.id
            AND event_type IN ('CREATED','RESCHEDULED')
            AND status IN ('PENDING','PROCESSING');
        END IF;
        RETURN NEW;
      END $fn$
    """)
    op.execute("""CREATE TRIGGER auto_outbox_supersede_stale
      AFTER INSERT ON notification_outbox
      FOR EACH ROW EXECUTE FUNCTION supersede_stale_auto_appointment_notifications()""")


def downgrade():
    # Never silently re-enable obsolete delivery when reverting a migration.
    conn = op.get_bind()
    if conn.execute(text(
        "SELECT 1 FROM notification_outbox WHERE status='SUPERSEDED' LIMIT 1"
    )).first() is not None:
        raise RuntimeError("superseded receipts exist: restore an isolated backup")
    op.execute("DROP TRIGGER auto_outbox_supersede_stale ON notification_outbox")
    op.execute("DROP TRIGGER auto_outbox_superseded_immutable ON notification_outbox")
    op.execute("DROP FUNCTION supersede_stale_auto_appointment_notifications()")
    op.execute("DROP FUNCTION guard_superseded_auto_outbox()")
    op.execute("ALTER TABLE notification_outbox DROP CONSTRAINT notification_outbox_status_check")
    op.execute("""ALTER TABLE notification_outbox ADD CONSTRAINT notification_outbox_status_check
      CHECK (status IN ('PENDING','PROCESSING','DELIVERED','DEAD'))""")
