"""Atomic scoped employee reservation for a work-bay booking.

Revision ID: 0049_auto_employee_reservation
Revises: 0048_auto_current_cancel
"""
from alembic import op

revision = "0049_auto_employee_reservation"
down_revision = "0048_auto_current_cancel"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
      CREATE TABLE auto_employee_reservation (
        id uuid PRIMARY KEY,
        booking_id uuid NOT NULL UNIQUE REFERENCES auto_bay_booking(id),
        organization_id uuid NOT NULL REFERENCES auto_organization(id),
        location_id uuid NOT NULL REFERENCES auto_service_location(id),
        employee_id uuid NOT NULL REFERENCES app_user(id),
        starts_at timestamptz NOT NULL,
        ends_at timestamptz NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(),
        canceled_at timestamptz,
        CHECK (ends_at>starts_at),
        CHECK (ends_at<=starts_at+interval '24 hours'),
        CHECK (canceled_at IS NULL OR canceled_at>=created_at)
      )
    """)
    op.execute("""
      ALTER TABLE auto_employee_reservation ADD CONSTRAINT
        ex_auto_employee_reservation_overlap
      EXCLUDE USING gist (
        employee_id WITH =,
        tstzrange(starts_at,ends_at,'[)') WITH &&
      ) WHERE (canceled_at IS NULL)
    """)
    op.execute("""
      CREATE FUNCTION guard_auto_employee_reservation()
      RETURNS trigger LANGUAGE plpgsql AS $fn$
      DECLARE booking record;
      BEGIN
        IF TG_OP='DELETE' THEN
          RAISE EXCEPTION 'employee reservation history deletion forbidden';
        END IF;
        IF TG_OP='UPDATE' THEN
          IF (NEW.id,NEW.booking_id,NEW.organization_id,NEW.location_id,
              NEW.employee_id,NEW.starts_at,NEW.ends_at,NEW.created_at)
             IS DISTINCT FROM
             (OLD.id,OLD.booking_id,OLD.organization_id,OLD.location_id,
              OLD.employee_id,OLD.starts_at,OLD.ends_at,OLD.created_at)
             OR (OLD.canceled_at IS NOT NULL AND
                 NEW.canceled_at IS DISTINCT FROM OLD.canceled_at)
             OR (OLD.canceled_at IS NULL AND NEW.canceled_at IS NULL
                 AND NEW IS DISTINCT FROM OLD) THEN
            RAISE EXCEPTION 'employee reservation identity immutable';
          END IF;
          RETURN NEW;
        END IF;
        SELECT organization_id,location_id,starts_at,ends_at,canceled_at
          INTO booking FROM auto_bay_booking
          WHERE id=NEW.booking_id FOR UPDATE;
        IF NOT FOUND OR booking.canceled_at IS NOT NULL
           OR (booking.organization_id,booking.location_id,
               booking.starts_at,booking.ends_at)
              IS DISTINCT FROM
              (NEW.organization_id,NEW.location_id,
               NEW.starts_at,NEW.ends_at)
           OR NOT EXISTS (
             SELECT 1 FROM auto_staff_shift s
             JOIN auto_service_location l ON l.id=s.location_id
             JOIN auto_organization o ON o.id=s.organization_id
             WHERE s.organization_id=NEW.organization_id
               AND s.location_id=NEW.location_id
               AND s.user_id=NEW.employee_id
               AND l.organization_id=o.id
               AND l.disabled_at IS NULL AND o.disabled_at IS NULL
               AND s.canceled_at IS NULL
               AND s.starts_at<=NEW.starts_at AND s.ends_at>=NEW.ends_at
             FOR SHARE OF s
           ) THEN
          RAISE EXCEPTION 'employee shift or scoped booking unavailable';
        END IF;
        RETURN NEW;
      END $fn$
    """)
    op.execute("""
      CREATE TRIGGER auto_employee_reservation_guard
      BEFORE INSERT OR UPDATE OR DELETE ON auto_employee_reservation
      FOR EACH ROW EXECUTE FUNCTION guard_auto_employee_reservation()
    """)
    op.execute("""
      CREATE FUNCTION release_auto_employee_reservation()
      RETURNS trigger LANGUAGE plpgsql AS $fn$
      BEGIN
        IF OLD.canceled_at IS NULL AND NEW.canceled_at IS NOT NULL THEN
          UPDATE auto_employee_reservation
             SET canceled_at=GREATEST(clock_timestamp(),created_at)
           WHERE booking_id=NEW.id AND canceled_at IS NULL;
        END IF;
        RETURN NEW;
      END $fn$
    """)
    op.execute("""
      CREATE TRIGGER auto_employee_reservation_release
      AFTER UPDATE OF canceled_at ON auto_bay_booking
      FOR EACH ROW EXECUTE FUNCTION release_auto_employee_reservation()
    """)


def downgrade():
    # Disposable synthetic PG17 only. This removes reservation history.
    op.execute("DROP TRIGGER auto_employee_reservation_release ON auto_bay_booking")
    op.execute("DROP FUNCTION release_auto_employee_reservation()")
    op.execute("DROP TABLE auto_employee_reservation")
    op.execute("DROP FUNCTION guard_auto_employee_reservation()")
