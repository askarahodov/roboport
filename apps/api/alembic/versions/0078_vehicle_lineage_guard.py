"""Verify synthetic promotion lineage at the database boundary.

Revision ID: 0078_vehicle_lineage_guard
Revises: 0077_vehicle_stage_promotion
"""
from alembic import op

revision = "0078_vehicle_lineage_guard"
down_revision = "0077_vehicle_stage_promotion"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
      CREATE FUNCTION guard_auto_vehicle_migration_promotion()
      RETURNS trigger LANGUAGE plpgsql AS $fn$
      BEGIN
        IF NOT EXISTS (
          SELECT 1 FROM auto_vehicle_migration_line l
          JOIN auto_vehicle_migration_batch b ON b.id=l.batch_id
          JOIN auto_service_location scope ON scope.id=l.location_id
          JOIN auto_organization o ON o.id=l.organization_id
          JOIN auto_vehicle v ON v.id=NEW.vehicle_id
          WHERE l.batch_id=NEW.batch_id AND l.row_number=NEW.row_number
            AND b.organization_id=l.organization_id
            AND scope.organization_id=l.organization_id
            AND scope.disabled_at IS NULL AND o.disabled_at IS NULL
            AND v.vin=l.vin AND v.make=l.make AND v.model=l.model
          FOR SHARE OF l,b,scope,o,v
        ) THEN
          RAISE EXCEPTION 'vehicle promotion lineage mismatch';
        END IF;
        RETURN NEW;
      END $fn$
    """)
    op.execute("""
      CREATE TRIGGER auto_vehicle_migration_promotion_integrity
      BEFORE INSERT ON auto_vehicle_migration_promotion
      FOR EACH ROW EXECUTE FUNCTION guard_auto_vehicle_migration_promotion()
    """)


def downgrade():
    op.execute("DROP TRIGGER auto_vehicle_migration_promotion_integrity ON auto_vehicle_migration_promotion")
    op.execute("DROP FUNCTION guard_auto_vehicle_migration_promotion()")
