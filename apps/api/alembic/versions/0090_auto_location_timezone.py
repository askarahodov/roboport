"""Часовой пояс филиала для текущего synthetic-календаря.

Revision ID: 0090_auto_location_timezone
Revises: 0089_auto_appointment_outbox
"""
from alembic import op

revision = "0090_auto_location_timezone"
down_revision = "0089_auto_appointment_outbox"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""ALTER TABLE auto_service_location
      ADD COLUMN timezone_name text NOT NULL DEFAULT 'UTC'
        CHECK (length(timezone_name) BETWEEN 3 AND 64)""")
    op.execute("""
      CREATE FUNCTION guard_auto_location_timezone()
      RETURNS trigger LANGUAGE plpgsql AS $fn$
      BEGIN
        IF NOT (NEW.timezone_name='UTC' OR
            (NEW.timezone_name ~ '^[A-Za-z][A-Za-z0-9_+-]*/[A-Za-z0-9_+/-]+$'
              AND EXISTS (SELECT 1 FROM pg_timezone_names
                          WHERE name=NEW.timezone_name))) THEN
          RAISE EXCEPTION 'invalid IANA location timezone';
        END IF;
        IF TG_OP='UPDATE' AND NEW.timezone_name IS DISTINCT FROM OLD.timezone_name THEN
          -- Не допускаем смены интерпретации календаря после первого события.
          IF EXISTS (SELECT 1 FROM auto_bay_booking
                     WHERE location_id=OLD.id LIMIT 1)
             OR EXISTS (SELECT 1 FROM auto_staff_shift
                        WHERE location_id=OLD.id LIMIT 1) THEN
            RAISE EXCEPTION 'location timezone fixed after scheduling';
          END IF;
        END IF;
        RETURN NEW;
      END $fn$
    """)
    op.execute("""CREATE TRIGGER auto_location_timezone_guard
      BEFORE INSERT OR UPDATE ON auto_service_location
      FOR EACH ROW EXECUTE FUNCTION guard_auto_location_timezone()""")


def downgrade():
    # Безопасен только на одноразовой синтетической БД до эксплуатации.
    op.execute("DROP TRIGGER auto_location_timezone_guard ON auto_service_location")
    op.execute("DROP FUNCTION guard_auto_location_timezone()")
    op.execute("ALTER TABLE auto_service_location DROP COLUMN timezone_name")
