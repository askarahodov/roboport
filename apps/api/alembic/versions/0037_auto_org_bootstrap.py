"""Immutable signed-bootstrap receipt for synthetic first ADMIN.

Revision ID: 0037_auto_org_bootstrap
Revises: 0036_auto_org_admin
"""
from alembic import op

revision = "0037_auto_org_bootstrap"
down_revision = "0036_auto_org_admin"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
      CREATE TABLE auto_org_bootstrap_receipt (
        id uuid PRIMARY KEY,
        organization_id uuid NOT NULL UNIQUE REFERENCES auto_organization(id),
        grant_id uuid NOT NULL UNIQUE REFERENCES auto_organization_role_grant(id),
        target_id uuid NOT NULL REFERENCES app_user(id),
        statement_sha256 char(64) NOT NULL CHECK (statement_sha256 ~ '^[a-f0-9]{64}$'),
        expires_at timestamptz NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now()
      )
    """)
    op.execute("""
      CREATE TRIGGER auto_org_bootstrap_receipt_immutable
      BEFORE UPDATE OR DELETE ON auto_org_bootstrap_receipt FOR EACH ROW
      EXECUTE FUNCTION reject_audit_mutation()
    """)


def downgrade():
    # Synthetic disposable database only.
    op.execute("DROP TABLE auto_org_bootstrap_receipt")
