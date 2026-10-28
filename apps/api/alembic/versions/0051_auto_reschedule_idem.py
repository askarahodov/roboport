"""Immutable synthetic reschedule request ledger.

Revision ID: 0051_auto_reschedule_idem
Revises: 0050_auto_appt_idempotency
"""
from alembic import op

revision = "0051_auto_reschedule_idem"
down_revision = "0050_auto_appt_idempotency"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
      CREATE TABLE auto_reschedule_idempotency (
        organization_id uuid NOT NULL REFERENCES auto_organization(id),
        actor_id uuid NOT NULL REFERENCES app_user(id),
        appointment_id uuid NOT NULL REFERENCES auto_appointment(id),
        request_key uuid NOT NULL,
        fingerprint text NOT NULL CHECK (fingerprint ~ '^[0-9a-f]{64}$'),
        new_booking_id uuid NOT NULL REFERENCES auto_bay_booking(id),
        created_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (organization_id,actor_id,appointment_id,request_key)
      )
    """)
    op.execute("""
      CREATE TRIGGER auto_reschedule_idempotency_immutable
      BEFORE UPDATE OR DELETE ON auto_reschedule_idempotency
      FOR EACH ROW EXECUTE FUNCTION immutable_auto_appointment_idempotency()
    """)


def downgrade():
    # Disposable synthetic PG17 only, destroys request history.
    op.execute("DROP TABLE auto_reschedule_idempotency")
