"""Неизменяемая идентичность и одноразовые переходы связи клиент–автомобиль.

Revision ID: 0133_auto_vehicle_link_guard
Revises: 0132_auto_crm_workflow

Сохраняет автомобильную модель с несколькими клиентскими связями на один автомобиль.
Не меняет старые robot tickets и не предоставляет production-доступ.
"""
from alembic import op

revision = "0133_auto_vehicle_link_guard"
down_revision = "0132_auto_crm_workflow"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
      CREATE FUNCTION guard_auto_vehicle_customer_link()
      RETURNS trigger LANGUAGE plpgsql AS $body$
      BEGIN
        IF TG_OP = 'INSERT' THEN
          IF NEW.verified_at IS NOT NULL OR NEW.verified_by IS NOT NULL
             OR NEW.revoked_at IS NOT NULL THEN
            RAISE EXCEPTION 'new auto vehicle customer link must start pending';
          END IF;
          RETURN NEW;
        END IF;

        IF (NEW.id, NEW.vehicle_id, NEW.customer_id, NEW.linked_at)
           IS DISTINCT FROM
           (OLD.id, OLD.vehicle_id, OLD.customer_id, OLD.linked_at) THEN
          RAISE EXCEPTION 'auto vehicle customer link identity immutable';
        END IF;

        IF OLD.verified_at IS NOT NULL THEN
          IF (NEW.verified_at, NEW.verified_by)
             IS DISTINCT FROM
             (OLD.verified_at, OLD.verified_by) THEN
            RAISE EXCEPTION 'auto vehicle customer link verification irreversible';
          END IF;
        ELSIF NEW.verified_at IS NOT NULL OR NEW.verified_by IS NOT NULL THEN
          IF NEW.verified_at IS NULL OR NEW.verified_by IS NULL THEN
            RAISE EXCEPTION 'auto vehicle customer link verification must be atomic';
          END IF;
          IF OLD.revoked_at IS NOT NULL OR NEW.revoked_at IS NOT NULL THEN
            RAISE EXCEPTION 'revoked auto vehicle customer link cannot be verified';
          END IF;
        END IF;

        IF OLD.revoked_at IS NOT NULL
           AND NEW.revoked_at IS DISTINCT FROM OLD.revoked_at THEN
          RAISE EXCEPTION 'auto vehicle customer link revocation irreversible';
        END IF;

        RETURN NEW;
      END $body$
    """)
    op.execute("""
      CREATE TRIGGER auto_vehicle_customer_link_guard
      BEFORE INSERT OR UPDATE ON auto_vehicle_customer_link
      FOR EACH ROW EXECUTE FUNCTION guard_auto_vehicle_customer_link()
    """)
    op.execute("""
      CREATE TRIGGER auto_vehicle_customer_link_no_delete
      BEFORE DELETE ON auto_vehicle_customer_link
      FOR EACH ROW EXECUTE FUNCTION reject_audit_mutation()
    """)


def downgrade():
    # Только disposable PostgreSQL: откат снимает защитные триггеры,
    # но не меняет и не удаляет строки автомобильных связей.
    op.execute("DROP TRIGGER auto_vehicle_customer_link_no_delete ON auto_vehicle_customer_link")
    op.execute("DROP TRIGGER auto_vehicle_customer_link_guard ON auto_vehicle_customer_link")
    op.execute("DROP FUNCTION guard_auto_vehicle_customer_link()")
