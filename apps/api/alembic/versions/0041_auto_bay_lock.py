"""Serialize bay disable with booking creation to close check/disable race.

Revision ID: 0041_auto_bay_lock
Revises: 0040_auto_bay_booking
"""
from alembic import op

revision = "0041_auto_bay_lock"
down_revision = "0040_auto_bay_booking"
branch_labels = None
depends_on = None


def _guard(lock: bool):
    lock_sql = " FOR SHARE OF b, l, o" if lock else ""
    op.execute(f"""
      CREATE OR REPLACE FUNCTION guard_auto_bay_booking()
      RETURNS trigger LANGUAGE plpgsql AS $fn$
      BEGIN
        IF TG_OP = 'DELETE' THEN
          RAISE EXCEPTION 'booking history deletion forbidden';
        END IF;
        IF TG_OP = 'UPDATE' THEN
          IF (NEW.id,NEW.organization_id,NEW.location_id,NEW.bay_id,
              NEW.starts_at,NEW.ends_at,NEW.created_at)
             IS DISTINCT FROM
             (OLD.id,OLD.organization_id,OLD.location_id,OLD.bay_id,
              OLD.starts_at,OLD.ends_at,OLD.created_at)
             OR (OLD.canceled_at IS NOT NULL AND
                 NEW.canceled_at IS DISTINCT FROM OLD.canceled_at) THEN
            RAISE EXCEPTION 'booking identity/cancellation immutable';
          END IF;
        ELSE
          IF NOT EXISTS (
            SELECT 1 FROM auto_work_bay b
            JOIN auto_service_location l ON l.id=b.location_id
            JOIN auto_organization o ON o.id=b.organization_id
            WHERE b.id=NEW.bay_id AND b.location_id=NEW.location_id
              AND b.organization_id=NEW.organization_id
              AND b.disabled_at IS NULL AND l.disabled_at IS NULL
              AND o.disabled_at IS NULL AND l.organization_id=o.id
            {lock_sql}
          ) THEN
            RAISE EXCEPTION 'bay not available within organization/location';
          END IF;
        END IF;
        RETURN NEW;
      END $fn$
    """)


def upgrade():
    _guard(True)


def downgrade():
    # For synthetic disposable database only.
    _guard(False)
