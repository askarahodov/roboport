"""Synthetic bay booking exclusion and immutable cancellation history.

Revision ID: 0040_auto_bay_booking
Revises: 0039_auto_shift_conflict
"""
from alembic import op

revision = "0040_auto_bay_booking"
down_revision = "0039_auto_shift_conflict"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
      CREATE TABLE auto_bay_booking (
        id uuid PRIMARY KEY,
        organization_id uuid NOT NULL REFERENCES auto_organization(id),
        location_id uuid NOT NULL REFERENCES auto_service_location(id),
        bay_id uuid NOT NULL REFERENCES auto_work_bay(id),
        starts_at timestamptz NOT NULL,
        ends_at timestamptz NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(),
        canceled_at timestamptz,
        CHECK (ends_at > starts_at),
        CHECK (ends_at <= starts_at + interval '24 hours'),
        CHECK (canceled_at IS NULL OR canceled_at >= created_at)
      )
    """)
    op.execute("""
      ALTER TABLE auto_bay_booking ADD CONSTRAINT ex_auto_bay_booking_overlap
      EXCLUDE USING gist (bay_id WITH =, tstzrange(starts_at,ends_at,'[)') WITH &&)
      WHERE (canceled_at IS NULL)
    """)
    op.execute("""
      CREATE FUNCTION guard_auto_bay_booking()
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
          ) THEN
            RAISE EXCEPTION 'bay not available within organization/location';
          END IF;
        END IF;
        RETURN NEW;
      END $fn$
    """)
    op.execute("""
      CREATE TRIGGER auto_bay_booking_guard BEFORE INSERT OR UPDATE OR DELETE
      ON auto_bay_booking FOR EACH ROW EXECUTE FUNCTION guard_auto_bay_booking()
    """)


def downgrade():
    # Disposable synthetic databases only.
    op.execute("DROP TABLE auto_bay_booking")
    op.execute("DROP FUNCTION guard_auto_bay_booking()")
