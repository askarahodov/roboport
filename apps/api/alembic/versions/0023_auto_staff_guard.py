"""Защита неизменяемости выдачи и отзыва staff-доступа к автомобилю.

Revision ID: 0023_auto_staff_guard
Revises: 0022_auto_vehicle_claim
"""
from alembic import op

revision = "0023_auto_staff_guard"
down_revision = "0022_auto_vehicle_claim"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
      CREATE FUNCTION guard_auto_vehicle_staff_assignment()
      RETURNS trigger LANGUAGE plpgsql AS $body$
      BEGIN
        IF (NEW.id,NEW.vehicle_id,NEW.user_id,NEW.role_code,NEW.created_at)
           IS DISTINCT FROM
           (OLD.id,OLD.vehicle_id,OLD.user_id,OLD.role_code,OLD.created_at) THEN
          RAISE EXCEPTION 'auto staff assignment identity immutable';
        END IF;
        IF OLD.revoked_at IS NOT NULL
           AND NEW.revoked_at IS DISTINCT FROM OLD.revoked_at THEN
          RAISE EXCEPTION 'auto staff assignment revocation irreversible';
        END IF;
        RETURN NEW;
      END $body$
    """)
    op.execute("""
      CREATE TRIGGER auto_vehicle_staff_assignment_guard
      BEFORE UPDATE ON auto_vehicle_staff_assignment
      FOR EACH ROW EXECUTE FUNCTION guard_auto_vehicle_staff_assignment()
    """)
    op.execute("""
      CREATE TRIGGER auto_vehicle_staff_assignment_no_delete
      BEFORE DELETE ON auto_vehicle_staff_assignment
      FOR EACH ROW EXECUTE FUNCTION reject_audit_mutation()
    """)


def downgrade():
    op.execute("DROP TRIGGER auto_vehicle_staff_assignment_no_delete ON auto_vehicle_staff_assignment")
    op.execute("DROP TRIGGER auto_vehicle_staff_assignment_guard ON auto_vehicle_staff_assignment")
    op.execute("DROP FUNCTION guard_auto_vehicle_staff_assignment()")
