"""Closed synthetic ADMIN authority and actor-attributed staff operation receipts.

Revision ID: 0036_auto_org_admin
Revises: 0035_auto_org_grant_audit

No public bootstrap, no legacy role mapping, no production delegation.
"""
from alembic import op

revision = "0036_auto_org_admin"
down_revision = "0035_auto_org_grant_audit"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
      ALTER TABLE auto_organization_role_grant
      DROP CONSTRAINT auto_organization_role_grant_role_code_check
    """)
    op.execute("""
      ALTER TABLE auto_organization_role_grant
      ADD CONSTRAINT auto_organization_role_grant_role_code_check
      CHECK (role_code IN ('ADMIN','MANAGER','INTAKE','MECHANIC','WAREHOUSE'))
    """)
    op.execute("""
      ALTER TABLE auto_organization_role_grant
      ADD CONSTRAINT ck_auto_org_admin_company_wide
      CHECK (role_code <> 'ADMIN' OR location_id IS NULL)
    """)
    op.execute("""
      CREATE TABLE auto_org_staff_action (
        id uuid PRIMARY KEY,
        grant_id uuid NOT NULL REFERENCES auto_organization_role_grant(id),
        organization_id uuid NOT NULL REFERENCES auto_organization(id),
        actor_id uuid NOT NULL REFERENCES app_user(id),
        action text NOT NULL CHECK (action IN ('GRANTED','REVOKED')),
        created_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE(grant_id, action)
      )
    """)
    op.execute("""
      CREATE TRIGGER auto_org_staff_action_immutable
      BEFORE UPDATE OR DELETE ON auto_org_staff_action FOR EACH ROW
      EXECUTE FUNCTION reject_audit_mutation()
    """)


def downgrade():
    # On disposable test databases only, NEVER on production audit data.
    op.execute("DROP TABLE auto_org_staff_action")
    op.execute("ALTER TABLE auto_organization_role_grant DROP CONSTRAINT ck_auto_org_admin_company_wide")
    op.execute("ALTER TABLE auto_organization_role_grant DROP CONSTRAINT auto_organization_role_grant_role_code_check")
    op.execute("""
      ALTER TABLE auto_organization_role_grant
      ADD CONSTRAINT auto_organization_role_grant_role_code_check
      CHECK (role_code IN ('MANAGER','INTAKE','MECHANIC','WAREHOUSE'))
    """)
