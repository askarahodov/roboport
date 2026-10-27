"""Аудит создания pending-связи клиента с автомобилем, без клиентских данных.

Revision ID: 0024_auto_link_intake
Revises: 0023_auto_staff_guard
"""
from alembic import op

revision = "0024_auto_link_intake"
down_revision = "0023_auto_staff_guard"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
      CREATE TABLE auto_vehicle_link_intake_audit (
        id uuid PRIMARY KEY,
        link_id uuid NOT NULL REFERENCES auto_vehicle_customer_link(id) ON DELETE RESTRICT,
        actor_id uuid NOT NULL REFERENCES app_user(id) ON DELETE RESTRICT,
        action text NOT NULL CHECK (action = 'LINK_CREATED'),
        created_at timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT ux_auto_vehicle_link_intake_audit_link UNIQUE (link_id)
      )
    """)
    op.execute("""
      CREATE TRIGGER auto_vehicle_link_intake_audit_immutable
      BEFORE UPDATE OR DELETE ON auto_vehicle_link_intake_audit
      FOR EACH ROW EXECUTE FUNCTION reject_audit_mutation()
    """)


def downgrade():
    # Только disposable dev-БД; таблицу с настоящим аудитом удалять нельзя.
    op.execute("DROP TABLE auto_vehicle_link_intake_audit")
