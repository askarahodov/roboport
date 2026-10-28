"""Attribute appointment cancellation to the live organization actor.

Revision ID: 0046_auto_appointment_actor
Revises: 0045_auto_work_order_events
"""
from alembic import op
revision="0046_auto_appointment_actor"
down_revision="0045_auto_work_order_events"
branch_labels=None
depends_on=None

def upgrade():
    op.execute("""
      CREATE TABLE auto_appointment_actor_action(
        id uuid PRIMARY KEY,
        appointment_id uuid NOT NULL REFERENCES auto_appointment(id),
        organization_id uuid NOT NULL REFERENCES auto_organization(id),
        actor_id uuid NOT NULL REFERENCES app_user(id),
        action text NOT NULL CHECK(action='CANCELED'),
        created_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE(appointment_id,action)
      )
    """)
    op.execute("""
      CREATE TRIGGER auto_appointment_actor_action_immutable
      BEFORE UPDATE OR DELETE ON auto_appointment_actor_action
      FOR EACH ROW EXECUTE FUNCTION reject_audit_mutation()
    """)

def downgrade():
    # Disposable synthetic PG17 only.
    op.execute("DROP TABLE auto_appointment_actor_action")
