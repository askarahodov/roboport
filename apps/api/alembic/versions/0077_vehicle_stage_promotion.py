"""Synthetic vehicle-only promotion lineage; no customer or history writes.

Revision ID: 0077_vehicle_stage_promotion
Revises: 0076_vehicle_stage_unique_vin
"""
from alembic import op

revision = "0077_vehicle_stage_promotion"
down_revision = "0076_vehicle_stage_unique_vin"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
      CREATE TABLE auto_vehicle_migration_promotion (
        batch_id uuid NOT NULL REFERENCES auto_vehicle_migration_batch(id),
        row_number integer NOT NULL,
        vehicle_id uuid NOT NULL UNIQUE REFERENCES auto_vehicle(id),
        created_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY(batch_id, row_number),
        FOREIGN KEY(batch_id, row_number)
          REFERENCES auto_vehicle_migration_line(batch_id, row_number)
      )
    """)
    op.execute("""
      CREATE TRIGGER auto_vehicle_migration_promotion_immutable
      BEFORE UPDATE OR DELETE ON auto_vehicle_migration_promotion
      FOR EACH ROW EXECUTE FUNCTION immutable_auto_stock_event()
    """)
    op.execute("REVOKE ALL ON auto_vehicle_migration_promotion FROM PUBLIC")


def downgrade():
    # Disposable synthetic fixture only; production removal requires a recovery plan.
    op.execute("DROP TABLE auto_vehicle_migration_promotion")
