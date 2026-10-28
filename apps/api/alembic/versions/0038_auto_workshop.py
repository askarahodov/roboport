"""Synthetic organization work bays and staff shifts; no public booking API.

Revision ID: 0038_auto_workshop
Revises: 0037_auto_org_bootstrap
"""
from alembic import op

revision = "0038_auto_workshop"
down_revision = "0037_auto_org_bootstrap"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
      CREATE TABLE auto_work_bay (
        id uuid PRIMARY KEY,
        organization_id uuid NOT NULL REFERENCES auto_organization(id),
        location_id uuid NOT NULL REFERENCES auto_service_location(id),
        code text NOT NULL CHECK (code ~ '^[A-Z0-9][A-Z0-9_-]{1,31}$'),
        created_at timestamptz NOT NULL DEFAULT now(),
        disabled_at timestamptz CHECK (disabled_at IS NULL OR disabled_at >= created_at),
        UNIQUE (location_id,code)
      )
    """)
    op.execute("""
      CREATE TABLE auto_staff_shift (
        id uuid PRIMARY KEY,
        organization_id uuid NOT NULL REFERENCES auto_organization(id),
        location_id uuid NOT NULL REFERENCES auto_service_location(id),
        user_id uuid NOT NULL REFERENCES app_user(id),
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
      CREATE FUNCTION guard_auto_workshop()
      RETURNS trigger LANGUAGE plpgsql AS $fn$
      BEGIN
        IF TG_OP = 'DELETE' THEN
          RAISE EXCEPTION 'workshop history deletion forbidden';
        END IF;
        IF TG_TABLE_NAME = 'auto_work_bay' THEN
          IF TG_OP = 'UPDATE' THEN
            IF (NEW.id,NEW.organization_id,NEW.location_id,NEW.code,NEW.created_at)
               IS DISTINCT FROM
               (OLD.id,OLD.organization_id,OLD.location_id,OLD.code,OLD.created_at)
               OR (OLD.disabled_at IS NOT NULL AND
                   NEW.disabled_at IS DISTINCT FROM OLD.disabled_at) THEN
              RAISE EXCEPTION 'bay identity/disable irreversible';
            END IF;
          END IF;
        ELSE
          IF TG_OP = 'UPDATE' THEN
            IF (NEW.id,NEW.organization_id,NEW.location_id,NEW.user_id,
                NEW.starts_at,NEW.ends_at,NEW.created_at)
               IS DISTINCT FROM
               (OLD.id,OLD.organization_id,OLD.location_id,OLD.user_id,
                OLD.starts_at,OLD.ends_at,OLD.created_at)
               OR (OLD.canceled_at IS NOT NULL AND
                   NEW.canceled_at IS DISTINCT FROM OLD.canceled_at) THEN
              RAISE EXCEPTION 'shift identity/cancellation irreversible';
            END IF;
          END IF;
        END IF;
        IF TG_OP = 'INSERT' AND NOT EXISTS (
          SELECT 1 FROM auto_service_location l
          JOIN auto_organization o ON o.id=l.organization_id
          WHERE l.id=NEW.location_id AND l.organization_id=NEW.organization_id
            AND l.disabled_at IS NULL AND o.disabled_at IS NULL
        ) THEN
          RAISE EXCEPTION 'location outside enabled organization';
        END IF;
        RETURN NEW;
      END $fn$
    """)
    for table in ("auto_work_bay", "auto_staff_shift"):
        op.execute(f"""
          CREATE TRIGGER {table}_guard BEFORE INSERT OR UPDATE OR DELETE
          ON {table} FOR EACH ROW EXECUTE FUNCTION guard_auto_workshop()
        """)
    op.execute("""
      CREATE INDEX ix_auto_staff_shift_location_start
      ON auto_staff_shift(organization_id,location_id,starts_at)
      WHERE canceled_at IS NULL
    """)


def downgrade():
    # Disposable synthetic PG17 only; discards operations history.
    op.execute("DROP TABLE auto_staff_shift")
    op.execute("DROP TABLE auto_work_bay")
    op.execute("DROP FUNCTION guard_auto_workshop()")
