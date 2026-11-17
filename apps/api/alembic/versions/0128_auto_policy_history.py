"""Индексированный доступ к действующей версии ACL и длинному immutable аудиту.

Revision ID: 0128_auto_policy_history
Revises: 0127_auto_accepted_release
Удаление только индексов при downgrade; события и роли не меняются.
"""
from alembic import op

revision="0128_auto_policy_history"
down_revision="0127_auto_accepted_release"
branch_labels=None
depends_on=None


def upgrade():
    op.execute("""
      CREATE INDEX auto_document_policy_scope_latest_idx
        ON auto_document_access_policy (
          organization_id,role_code,document_kind,capability,
          location_id,user_id,created_at DESC,id DESC
        )
    """)
    op.execute("""
      CREATE INDEX auto_document_policy_tenant_audit_idx
        ON auto_document_access_policy (
          organization_id,created_at DESC,id DESC
        )
    """)


def downgrade():
    # Никаких DELETE/UPDATE в immutable истории при SQL rollback.
    op.execute("DROP INDEX auto_document_policy_tenant_audit_idx")
    op.execute("DROP INDEX auto_document_policy_scope_latest_idx")
