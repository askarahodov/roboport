"""Idempotent synthetic appointment request ledger.

Revision ID: 0050_auto_appt_idempotency
Revises: 0049_auto_employee_reservation
"""
from alembic import op

revision = "0050_auto_appt_idempotency"
down_revision = "0049_auto_employee_reservation"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
      CREATE TABLE auto_appointment_idempotency (
        organization_id uuid NOT NULL REFERENCES auto_organization(id),
        actor_id uuid NOT NULL REFERENCES app_user(id),
        request_key uuid NOT NULL,
        fingerprint text NOT NULL CHECK (fingerprint ~ '^[0-9a-f]{64}$'),
        appointment_id uuid NOT NULL,
        booking_id uuid NOT NULL,
        work_order_id uuid NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (organization_id,actor_id,request_key)
      )
    """)
    op.execute("""
      CREATE FUNCTION immutable_auto_appointment_idempotency()
      RETURNS trigger LANGUAGE plpgsql AS $fn$
      BEGIN
        RAISE EXCEPTION 'idempotency history immutable';
      END $fn$
    """)
    op.execute("""
      CREATE TRIGGER auto_appointment_idempotency_immutable
      BEFORE UPDATE OR DELETE ON auto_appointment_idempotency
      FOR EACH ROW EXECUTE FUNCTION immutable_auto_appointment_idempotency()
    """)


def downgrade():
    # Synthetic disposable databases only.
    op.execute("DROP TABLE auto_appointment_idempotency")
    op.execute("DROP FUNCTION immutable_auto_appointment_idempotency()")
