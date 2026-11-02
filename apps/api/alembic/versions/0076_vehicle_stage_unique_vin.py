"""Prevent same synthetic VIN entering more than one staging batch concurrently.

Revision ID: 0076_vehicle_stage_unique_vin
Revises: 0075_auto_vehicle_stage
"""
from alembic import op

revision = "0076_vehicle_stage_unique_vin"
down_revision = "0075_auto_vehicle_stage"
branch_labels = None
depends_on = None


def upgrade():
    # The staging domain is synthetic-only. Globally unique VINs prevent
    # two distinct batches from claiming the same identity, even concurrently.
    op.execute("""
      CREATE UNIQUE INDEX ux_auto_vehicle_migration_vin_global
      ON auto_vehicle_migration_line (vin)
    """)


def downgrade():
    op.execute("DROP INDEX ux_auto_vehicle_migration_vin_global")
