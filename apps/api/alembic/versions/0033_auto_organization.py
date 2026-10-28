"""Аддитивный каркас автомобильных организаций; только synthetic/dev.

Revision ID: 0033_auto_organization
Revises: 0032_auto_manager_approval

Не назначает ADMIN, не переносит legacy grants или клиентские ПДн.
"""
from alembic import op

revision = "0033_auto_organization"
down_revision = "0032_auto_manager_approval"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
      CREATE TABLE auto_organization (
        id uuid PRIMARY KEY,
        code text NOT NULL UNIQUE
          CHECK (code ~ '^[A-Z0-9][A-Z0-9_-]{1,31}$'),
        display_name text NOT NULL
          CHECK (length(btrim(display_name)) BETWEEN 1 AND 160),
        created_at timestamptz NOT NULL DEFAULT now(),
        disabled_at timestamptz
          CHECK (disabled_at IS NULL OR disabled_at >= created_at)
      )
    """)
    # Existing synthetic locations deliberately remain unassigned, not
    # silently placed in an organization that could gain access to them.
    op.execute("""
      ALTER TABLE auto_service_location
      ADD COLUMN organization_id uuid
        REFERENCES auto_organization(id) ON DELETE RESTRICT
    """)
    op.execute("""
      CREATE INDEX ix_auto_service_location_organization
      ON auto_service_location(organization_id, id)
      WHERE organization_id IS NOT NULL
    """)
    op.execute("""
      CREATE FUNCTION guard_auto_organization()
      RETURNS trigger LANGUAGE plpgsql AS $fn$
      BEGIN
        IF (NEW.id,NEW.code,NEW.display_name,NEW.created_at)
          IS DISTINCT FROM
          (OLD.id,OLD.code,OLD.display_name,OLD.created_at) THEN
          RAISE EXCEPTION 'auto organization identity immutable';
        END IF;
        IF OLD.disabled_at IS NOT NULL AND
          NEW.disabled_at IS DISTINCT FROM OLD.disabled_at THEN
          RAISE EXCEPTION 'auto organization disable irreversible';
        END IF;
        RETURN NEW;
      END $fn$
    """)
    op.execute("""
      CREATE TRIGGER auto_organization_guard BEFORE UPDATE
      ON auto_organization FOR EACH ROW
      EXECUTE FUNCTION guard_auto_organization()
    """)
    op.execute("""
      CREATE TRIGGER auto_organization_no_delete BEFORE DELETE
      ON auto_organization FOR EACH ROW
      EXECUTE FUNCTION reject_audit_mutation()
    """)
    # The old location guard has no organization_id: bind-once rule must
    # be enforced independently and includes protection against reassignment.
    op.execute("""
      CREATE FUNCTION guard_auto_location_organization()
      RETURNS trigger LANGUAGE plpgsql AS $fn$
      BEGIN
        IF OLD.organization_id IS NOT NULL AND
           NEW.organization_id IS DISTINCT FROM OLD.organization_id THEN
          RAISE EXCEPTION 'auto location organization immutable';
        END IF;
        RETURN NEW;
      END $fn$
    """)
    op.execute("""
      CREATE TRIGGER auto_location_organization_guard BEFORE UPDATE
      ON auto_service_location FOR EACH ROW
      EXECUTE FUNCTION guard_auto_location_organization()
    """)


def downgrade():
    # Only disposable test DB; live removal needs independent migration.
    op.execute("DROP TRIGGER auto_location_organization_guard ON auto_service_location")
    op.execute("DROP FUNCTION guard_auto_location_organization()")
    op.execute("DROP INDEX ix_auto_service_location_organization")
    op.execute("ALTER TABLE auto_service_location DROP COLUMN organization_id")
    op.execute("DROP TABLE auto_organization")
    op.execute("DROP FUNCTION guard_auto_organization()")
