"""Audit organization role grant creation/revocation, synthetic only.

Revision ID: 0035_auto_org_grant_audit
Revises: 0034_auto_org_grants
"""
from alembic import op

revision = "0035_auto_org_grant_audit"
down_revision = "0034_auto_org_grants"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
      CREATE TABLE auto_organization_grant_audit (
        id bigserial PRIMARY KEY,
        grant_id uuid NOT NULL REFERENCES auto_organization_role_grant(id),
        organization_id uuid NOT NULL REFERENCES auto_organization(id),
        actor_id uuid REFERENCES app_user(id),
        action text NOT NULL CHECK (action IN ('GRANTED','REVOKED')),
        recorded_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE(grant_id,action)
      )
    """)
    op.execute("""
      CREATE FUNCTION auto_org_grant_audit_trigger()
      RETURNS trigger LANGUAGE plpgsql AS $fn$
      BEGIN
        IF TG_OP = 'INSERT' THEN
          INSERT INTO auto_organization_grant_audit(grant_id,organization_id,action)
          VALUES(NEW.id,NEW.organization_id,'GRANTED');
        ELSIF OLD.revoked_at IS NULL AND NEW.revoked_at IS NOT NULL THEN
          INSERT INTO auto_organization_grant_audit(grant_id,organization_id,action)
          VALUES(NEW.id,NEW.organization_id,'REVOKED');
        END IF;
        RETURN NEW;
      END $fn$
    """)
    op.execute("""
      CREATE TRIGGER auto_org_grant_audit_event AFTER INSERT OR UPDATE
      ON auto_organization_role_grant FOR EACH ROW
      EXECUTE FUNCTION auto_org_grant_audit_trigger()
    """)
    op.execute("""
      CREATE TRIGGER auto_org_grant_audit_immutable BEFORE UPDATE OR DELETE
      ON auto_organization_grant_audit FOR EACH ROW
      EXECUTE FUNCTION reject_audit_mutation()
    """)


def downgrade():
    # Only disposable synthetic databases; destroys audit evidence.
    op.execute("DROP TRIGGER auto_org_grant_audit_event ON auto_organization_role_grant")
    op.execute("DROP TABLE auto_organization_grant_audit")
    op.execute("DROP FUNCTION auto_org_grant_audit_trigger()")
