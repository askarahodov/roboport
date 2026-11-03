"""Immutable synthetic cancellation receipt.

Revision ID: 0095_auto_cancel_idempotency
Revises: 0094_auto_inventory_activation
"""
from alembic import op

revision="0095_auto_cancel_idempotency"
down_revision="0094_auto_inventory_activation"
branch_labels=None
depends_on=None

def upgrade():
    op.execute("""
      CREATE TABLE auto_cancel_idempotency (
        organization_id uuid NOT NULL REFERENCES auto_organization(id),
        actor_id uuid NOT NULL REFERENCES app_user(id),
        request_key uuid NOT NULL,
        appointment_id uuid NOT NULL REFERENCES auto_appointment(id),
        booking_id uuid NOT NULL REFERENCES auto_bay_booking(id),
        created_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (organization_id,actor_id,request_key)
      )
    """)
    op.execute("""
      CREATE FUNCTION immutable_auto_cancel_idempotency()
      RETURNS trigger LANGUAGE plpgsql AS $fn$
      BEGIN
        RAISE EXCEPTION 'cancel receipt immutable';
      END $fn$
    """)
    op.execute("""
      CREATE TRIGGER auto_cancel_idempotency_immutable
      BEFORE UPDATE OR DELETE ON auto_cancel_idempotency
      FOR EACH ROW EXECUTE FUNCTION immutable_auto_cancel_idempotency()
    """)

def downgrade():
    # Disposable synthetic DB only.
    op.execute("DROP TABLE auto_cancel_idempotency")
    op.execute("DROP FUNCTION immutable_auto_cancel_idempotency()")
