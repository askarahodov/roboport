"""Synthetic work-order service selection from branch catalog.

Revision ID: 0082_order_service
Revises: 0081_auto_service_catalog
"""
from alembic import op
revision = "0082_order_service"
down_revision = "0081_auto_service_catalog"
branch_labels = None
depends_on = None

def upgrade():
    op.execute("""
      CREATE TABLE auto_work_order_service (
        id uuid PRIMARY KEY,
        work_order_id uuid NOT NULL REFERENCES auto_work_order(id),
        service_id uuid NOT NULL REFERENCES auto_service_catalog(id),
        organization_id uuid NOT NULL REFERENCES auto_organization(id),
        location_id uuid NOT NULL REFERENCES auto_service_location(id),
        created_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE(work_order_id,service_id)
      )
    """)
    op.execute("""
      CREATE FUNCTION guard_auto_work_order_service()
      RETURNS trigger LANGUAGE plpgsql AS $fn$
      BEGIN
        IF NOT EXISTS(
          SELECT 1 FROM auto_work_order w
          JOIN auto_service_catalog s
            ON s.id=NEW.service_id
           AND s.organization_id=w.organization_id
           AND s.location_id=w.location_id
          JOIN auto_service_location l ON l.id=w.location_id
          JOIN auto_organization o ON o.id=w.organization_id
          WHERE w.id=NEW.work_order_id
          AND w.organization_id=NEW.organization_id
          AND w.location_id=NEW.location_id
          AND l.organization_id=w.organization_id
          AND l.disabled_at IS NULL AND o.disabled_at IS NULL
          FOR SHARE OF w,s,l,o
        ) THEN RAISE EXCEPTION 'work order service scope mismatch'; END IF;
        RETURN NEW;
      END $fn$
    """)
    op.execute("""
      CREATE TRIGGER auto_work_order_service_scope
      BEFORE INSERT ON auto_work_order_service
      FOR EACH ROW EXECUTE FUNCTION guard_auto_work_order_service()
    """)
    op.execute("""
      CREATE TRIGGER auto_work_order_service_immutable
      BEFORE UPDATE OR DELETE ON auto_work_order_service
      FOR EACH ROW EXECUTE FUNCTION immutable_auto_stock_event()
    """)
    op.execute("REVOKE ALL ON auto_work_order_service FROM PUBLIC")

def downgrade():
    op.execute("DROP TABLE auto_work_order_service")
    op.execute("DROP FUNCTION guard_auto_work_order_service()")
