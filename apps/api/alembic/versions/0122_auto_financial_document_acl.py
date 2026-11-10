"""Extend scoped document ACL to synthetic financial documents and CASHIER.

Revision ID: 0122_auto_financial_document_acl
Revises: 0121_auto_public_booking
No monetary or production enablement. An append-only policy remains restrictive.
"""
from alembic import op

revision="0122_auto_financial_document_acl"
down_revision="0121_auto_public_booking"
branch_labels=None
depends_on=None


def upgrade():
    op.execute("""ALTER TABLE auto_document_access_policy
      DROP CONSTRAINT auto_document_access_policy_role_code_check""")
    op.execute("""ALTER TABLE auto_document_access_policy
      ADD CONSTRAINT auto_document_access_policy_role_code_check
      CHECK(role_code IN ('MANAGER','INTAKE','MECHANIC','WAREHOUSE','CASHIER'))""")
    op.execute("""ALTER TABLE auto_document_access_policy
      DROP CONSTRAINT auto_document_access_policy_document_kind_check""")
    op.execute("""ALTER TABLE auto_document_access_policy
      ADD CONSTRAINT auto_document_access_policy_document_kind_check
      CHECK(document_kind IN ('INTAKE_ACT','ESTIMATE','HANDOVER','SUPPLIER_ORDER','FINANCE'))""")


def downgrade():
    # Safe for disposable PG17 only. Never silently delete append-only audit:
    # preserve history and refuse to roll back if the new types were used.
    op.execute("""DO $$
      BEGIN
        IF EXISTS (SELECT 1 FROM auto_document_access_policy
                   WHERE role_code='CASHIER' OR document_kind='FINANCE')
        THEN RAISE EXCEPTION 'cannot downgrade active financial ACL audit'; END IF;
      END $$""")
    op.execute("""ALTER TABLE auto_document_access_policy
      DROP CONSTRAINT auto_document_access_policy_document_kind_check""")
    op.execute("""ALTER TABLE auto_document_access_policy
      ADD CONSTRAINT auto_document_access_policy_document_kind_check
      CHECK(document_kind IN ('INTAKE_ACT','ESTIMATE','HANDOVER','SUPPLIER_ORDER'))""")
    op.execute("""ALTER TABLE auto_document_access_policy
      DROP CONSTRAINT auto_document_access_policy_role_code_check""")
    op.execute("""ALTER TABLE auto_document_access_policy
      ADD CONSTRAINT auto_document_access_policy_role_code_check
      CHECK(role_code IN ('MANAGER','INTAKE','MECHANIC','WAREHOUSE'))""")
