"""Add scoped staff grant ledger for synthetic automotive organizations.

Revision ID: 0034_auto_org_grants
Revises: 0033_auto_organization
"""
from alembic import op

revision = "0034_auto_org_grants"
down_revision = "0033_auto_organization"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
      CREATE TABLE auto_organization_role_grant (
        id uuid PRIMARY KEY,
        organization_id uuid NOT NULL REFERENCES auto_organization(id),
        location_id uuid REFERENCES auto_service_location(id),
        user_id uuid NOT NULL REFERENCES app_user(id),
        role_code text NOT NULL CHECK (role_code IN ('MANAGER','INTAKE','MECHANIC','WAREHOUSE')),
        created_at timestamptz NOT NULL DEFAULT now(),
        revoked_at timestamptz CHECK (revoked_at IS NULL OR revoked_at >= created_at)
      )
    """)
    op.execute("""
      CREATE UNIQUE INDEX ux_auto_organization_grant_active
      ON auto_organization_role_grant(organization_id,location_id,user_id,role_code)
      NULLS NOT DISTINCT WHERE revoked_at IS NULL
    """)
    op.execute("""
      CREATE FUNCTION auto_organization_grant_guard()
      RETURNS trigger LANGUAGE plpgsql AS $$
      BEGIN
        IF TG_OP = 'DELETE' THEN
          RAISE EXCEPTION 'grant deletion forbidden';
        END IF;
        IF TG_OP = 'INSERT' THEN
          IF NOT EXISTS (SELECT 1 FROM auto_organization o
                         WHERE o.id=NEW.organization_id AND o.disabled_at IS NULL) THEN
            RAISE EXCEPTION 'organization unavailable';
          END IF;
          IF NEW.location_id IS NOT NULL AND NOT EXISTS
             (SELECT 1 FROM auto_service_location l
              WHERE l.id=NEW.location_id AND l.organization_id=NEW.organization_id
                AND l.disabled_at IS NULL) THEN
            RAISE EXCEPTION 'location organization mismatch';
          END IF;
        ELSE
          IF (NEW.id,NEW.organization_id,NEW.location_id,NEW.user_id,
              NEW.role_code,NEW.created_at)
             IS DISTINCT FROM
             (OLD.id,OLD.organization_id,OLD.location_id,OLD.user_id,
              OLD.role_code,OLD.created_at) THEN
            RAISE EXCEPTION 'grant identity immutable';
          END IF;
          IF OLD.revoked_at IS NOT NULL AND
             NEW.revoked_at IS DISTINCT FROM OLD.revoked_at THEN
            RAISE EXCEPTION 'revocation irreversible';
          END IF;
        END IF;
        RETURN NEW;
      END $$
    """)
    op.execute("""
      CREATE TRIGGER auto_org_grant_guard BEFORE INSERT OR UPDATE OR DELETE
      ON auto_organization_role_grant FOR EACH ROW
      EXECUTE FUNCTION auto_organization_grant_guard()
    """)


def downgrade():
    # Only isolated synthetic PG17, never production.
    op.execute("DROP TABLE auto_organization_role_grant")
    op.execute("DROP FUNCTION auto_organization_grant_guard()")
