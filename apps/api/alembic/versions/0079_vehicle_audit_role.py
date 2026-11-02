"""Synthetic migration metadata auditor: explicit least-privilege PostgreSQL role.

Revision ID: 0079_vehicle_audit_role
Revises: 0078_vehicle_lineage_guard

No login, no customer/VIN/line/vehicle permissions. No production deployment approval.
"""
from alembic import op

revision = "0079_vehicle_audit_role"
down_revision = "0078_vehicle_lineage_guard"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
      DO $$
      BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_roles
                       WHERE rolname='roboport_vehicle_migration_auditor') THEN
          CREATE ROLE roboport_vehicle_migration_auditor NOLOGIN
            NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOINHERIT;
        END IF;
      END $$
    """)
    # The role intentionally has no INSERT/UPDATE/DELETE, no VIN access
    # and no privileges on the live automotive domain.
    op.execute("""
      GRANT SELECT (id, organization_id, source_sha256, row_count, created_at)
      ON auto_vehicle_migration_batch
      TO roboport_vehicle_migration_auditor
    """)
    for table in ("auto_vehicle_migration_line",
                  "auto_vehicle_migration_promotion", "auto_vehicle",
                  "auto_vehicle_customer_link"):
        op.execute(f"REVOKE ALL ON {table} FROM roboport_vehicle_migration_auditor")


def downgrade():
    # Disposable synthetic database only. Roles are cluster-scoped.
    op.execute("""
      REVOKE SELECT (id, organization_id, source_sha256, row_count, created_at)
      ON auto_vehicle_migration_batch
      FROM roboport_vehicle_migration_auditor
    """)
    op.execute("DROP ROLE roboport_vehicle_migration_auditor")
