"""Extend delegated ACL to estimates.

Revision ID: 0010_estimate_acl
Revises: 0009_attachments
"""
from alembic import op

revision="0010_estimate_acl"
down_revision="0009_attachments"
branch_labels=None
depends_on=None


def upgrade():
    op.execute("ALTER TABLE acl_entry DROP CONSTRAINT acl_entry_action_check")
    op.execute("""ALTER TABLE acl_entry ADD CONSTRAINT acl_entry_action_check CHECK
        (action IN ('ticket.read','ticket.diagnose','ticket.intake','ticket.manage',
                    'estimate.read','estimate.approve'))""")


def downgrade():
    op.execute("DELETE FROM acl_entry WHERE action IN ('estimate.read','estimate.approve')")
    op.execute("ALTER TABLE acl_entry DROP CONSTRAINT acl_entry_action_check")
    op.execute("""ALTER TABLE acl_entry ADD CONSTRAINT acl_entry_action_check CHECK
        (action IN ('ticket.read','ticket.diagnose','ticket.intake','ticket.manage'))""")
