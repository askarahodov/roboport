"""Транзакционные synthetic-уведомления о записи, переносе и отмене.

Revision ID: 0089_auto_appointment_outbox
Revises: 0088_auto_demo_close

Только dev sink; никакой отправки реальных SMS/email и никакого PII.
"""
from alembic import op

revision = "0089_auto_appointment_outbox"
down_revision = "0088_auto_demo_close"
branch_labels = None
depends_on = None


def upgrade():
    # Старые ticket/estimate типы сохраняются: расширение без разрыва legacy.
    op.execute("ALTER TABLE notification_outbox DROP CONSTRAINT notification_outbox_aggregate_type_check")
    op.execute("""ALTER TABLE notification_outbox ADD CONSTRAINT notification_outbox_aggregate_type_check
      CHECK (aggregate_type IN ('ticket','estimate','auto_appointment'))""")
    op.execute("ALTER TABLE notification_outbox DROP CONSTRAINT notification_outbox_event_type_check")
    op.execute("""ALTER TABLE notification_outbox ADD CONSTRAINT notification_outbox_event_type_check
      CHECK (event_type IN ('CREATED','STATE_CHANGED','RESCHEDULED','CANCELED'))""")
    op.execute("""CREATE INDEX ix_auto_appointment_outbox_aggregate
      ON notification_outbox(aggregate_id,created_at)
      WHERE aggregate_type='auto_appointment'""")

    op.execute("""
      CREATE FUNCTION enqueue_auto_appointment_event()
      RETURNS trigger LANGUAGE plpgsql AS $fn$
      DECLARE kind text;
      BEGIN
        kind = CASE WHEN NEW.action='CREATED' THEN 'CREATED' ELSE 'CANCELED' END;
        INSERT INTO notification_outbox(event_key,aggregate_type,aggregate_id,event_type,payload)
          VALUES ('auto-appointment:event:' || NEW.id::text,
                  'auto_appointment',NEW.appointment_id,kind,
                  jsonb_build_object('action',kind))
          ON CONFLICT(event_key) DO NOTHING;
        RETURN NEW;
      END $fn$
    """)
    op.execute("""CREATE TRIGGER auto_appointment_outbox_event
      AFTER INSERT ON auto_appointment_event
      FOR EACH ROW EXECUTE FUNCTION enqueue_auto_appointment_event()""")

    op.execute("""
      CREATE FUNCTION enqueue_auto_appointment_reschedule()
      RETURNS trigger LANGUAGE plpgsql AS $fn$
      BEGIN
        INSERT INTO notification_outbox(event_key,aggregate_type,aggregate_id,event_type,payload)
          VALUES ('auto-appointment:move:' || NEW.id::text,
                  'auto_appointment',NEW.appointment_id,'RESCHEDULED',
                  jsonb_build_object('action','RESCHEDULED'))
          ON CONFLICT(event_key) DO NOTHING;
        RETURN NEW;
      END $fn$
    """)
    op.execute("""CREATE TRIGGER auto_appointment_outbox_reschedule
      AFTER INSERT ON auto_appointment_reschedule
      FOR EACH ROW EXECUTE FUNCTION enqueue_auto_appointment_reschedule()""")


def downgrade():
    # Сохраняем audit/outbox данные и не стираем отправленные receipts.
    conn = op.get_bind()
    pending = conn.execute(__import__("sqlalchemy").text(
        "SELECT 1 FROM notification_outbox WHERE aggregate_type='auto_appointment' LIMIT 1"
    )).first()
    if pending is not None:
        raise RuntimeError("auto appointment outbox receipts exist: restore an isolated backup")
    op.execute("DROP TRIGGER auto_appointment_outbox_reschedule ON auto_appointment_reschedule")
    op.execute("DROP TRIGGER auto_appointment_outbox_event ON auto_appointment_event")
    op.execute("DROP FUNCTION enqueue_auto_appointment_reschedule()")
    op.execute("DROP FUNCTION enqueue_auto_appointment_event()")
    op.execute("DROP INDEX ix_auto_appointment_outbox_aggregate")
    op.execute("ALTER TABLE notification_outbox DROP CONSTRAINT notification_outbox_event_type_check")
    op.execute("""ALTER TABLE notification_outbox ADD CONSTRAINT notification_outbox_event_type_check
      CHECK (event_type IN ('CREATED','STATE_CHANGED'))""")
    op.execute("ALTER TABLE notification_outbox DROP CONSTRAINT notification_outbox_aggregate_type_check")
    op.execute("""ALTER TABLE notification_outbox ADD CONSTRAINT notification_outbox_aggregate_type_check
      CHECK (aggregate_type IN ('ticket','estimate'))""")
