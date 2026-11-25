"""Новые резервы сотрудника требуют активной учётной записи (#113).

Исторические брони сохраняются; блокировка сотрудника не отменяет запись.
"""
from alembic import op

revision = "0144_auto_booking_active_staff"
down_revision = "0143_auto_vehicle_link_time"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
      CREATE FUNCTION guard_auto_booking_active_staff()
      RETURNS trigger LANGUAGE plpgsql AS $fn$
      BEGIN
        -- SHARE сериализует проверку с изменением статуса app_user.
        IF NOT EXISTS (
          SELECT 1 FROM app_user
          WHERE id=NEW.employee_id AND status='ACTIVE' FOR SHARE
        ) THEN
          RAISE EXCEPTION 'booking employee inactive';
        END IF;
        RETURN NEW;
      END $fn$
    """)
    op.execute("""
      CREATE TRIGGER auto_booking_active_staff_guard
      BEFORE INSERT ON auto_employee_reservation
      FOR EACH ROW EXECUTE FUNCTION guard_auto_booking_active_staff()
    """)


def downgrade():
    op.execute("DROP TRIGGER auto_booking_active_staff_guard ON auto_employee_reservation")
    op.execute("DROP FUNCTION guard_auto_booking_active_staff()")
