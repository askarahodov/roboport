"""Неизменяемый аудит делегирования INTAKE на конкретный автомобиль.

Revision ID: 0025_auto_intake_delegation
Revises: 0024_auto_link_intake
"""
from alembic import op

revision = "0025_auto_intake_delegation"
down_revision = "0024_auto_link_intake"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
      CREATE TABLE auto_vehicle_staff_assignment_audit (
        id uuid PRIMARY KEY,
        assignment_id uuid NOT NULL REFERENCES auto_vehicle_staff_assignment(id) ON DELETE RESTRICT,
        vehicle_id uuid NOT NULL REFERENCES auto_vehicle(id) ON DELETE RESTRICT,
        actor_id uuid NOT NULL REFERENCES app_user(id) ON DELETE RESTRICT,
        target_user_id uuid NOT NULL REFERENCES app_user(id) ON DELETE RESTRICT,
        action text NOT NULL CHECK(action IN ('INTAKE_GRANTED','INTAKE_REVOKED')),
        created_at timestamptz NOT NULL DEFAULT now()
      )
    """)
    op.execute("""
      CREATE INDEX ix_auto_intake_delegation_audit
      ON auto_vehicle_staff_assignment_audit(vehicle_id,created_at)
    """)
    op.execute("""
      CREATE TRIGGER auto_vehicle_staff_assignment_audit_immutable
      BEFORE UPDATE OR DELETE ON auto_vehicle_staff_assignment_audit
      FOR EACH ROW EXECUTE FUNCTION reject_audit_mutation()
    """)


def downgrade():
    # Только на одноразовом PG17 стенде: production rollback здесь не предусмотрен.
    op.execute("DROP TABLE auto_vehicle_staff_assignment_audit")
