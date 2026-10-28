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
    op.execute("""
      CREATE TRIGGER auto_appointment_reschedule_immutable
      BEFORE UPDATE OR DELETE ON auto_appointment_reschedule
      FOR EACH ROW EXECUTE FUNCTION reject_audit_mutation()
    """)

def downgrade():
    # Disposable synthetic database only. Never discard production audit data.
    op.execute("DROP TABLE auto_appointment_reschedule")
