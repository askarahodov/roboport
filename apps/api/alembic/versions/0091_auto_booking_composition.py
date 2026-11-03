"""Атомарная композиция записи: услуги и запросы материалов без списания склада.

Revision ID: 0091_auto_booking_composition
Revises: 0090_auto_location_timezone

Только synthetic; физический stock ledger остаётся независимым и неизменным.
"""
from alembic import op
from sqlalchemy import text

revision = "0091_auto_booking_composition"
down_revision = "0090_auto_location_timezone"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""ALTER TABLE auto_work_order_service
      ADD COLUMN origin text NOT NULL DEFAULT 'STAFF'
      CHECK (origin IN ('STAFF','BOOKING'))""")
    op.execute("""
      CREATE TABLE auto_work_order_material_request (
        id uuid PRIMARY KEY,
        work_order_id uuid NOT NULL REFERENCES auto_work_order(id),
        sku_id uuid NOT NULL REFERENCES auto_stock_sku(id),
        organization_id uuid NOT NULL REFERENCES auto_organization(id),
        location_id uuid NOT NULL REFERENCES auto_service_location(id),
        kind text NOT NULL CHECK (kind IN ('PART','MATERIAL')),
        quantity integer NOT NULL CHECK (quantity BETWEEN 1 AND 1000),
        created_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE(work_order_id,sku_id,kind)
      )
    """)
    op.execute("""
      CREATE FUNCTION guard_auto_booking_material_request()
      RETURNS trigger LANGUAGE plpgsql AS $fn$
      BEGIN
        IF NOT EXISTS(
          SELECT 1 FROM auto_work_order w
          JOIN auto_appointment a ON a.id=w.appointment_id
          JOIN LATERAL (
            SELECT COALESCE((
              SELECT rs.new_booking_id FROM auto_appointment_reschedule rs
              WHERE rs.appointment_id=a.id
              ORDER BY rs.created_at DESC,rs.id DESC LIMIT 1
            ),a.booking_id) AS booking_id
          ) current_booking ON TRUE
          JOIN auto_bay_booking b ON b.id=current_booking.booking_id
          JOIN auto_vehicle_customer_link proof ON proof.id=a.customer_link_id
            AND proof.vehicle_id=a.vehicle_id
            AND proof.verified_at IS NOT NULL AND proof.revoked_at IS NULL
          JOIN auto_stock_sku sku ON sku.id=NEW.sku_id
            AND sku.organization_id=w.organization_id
            AND sku.location_id=w.location_id
          JOIN auto_service_location loc ON loc.id=w.location_id
            AND loc.organization_id=w.organization_id AND loc.disabled_at IS NULL
          JOIN auto_organization org ON org.id=w.organization_id
            AND org.disabled_at IS NULL
          WHERE w.id=NEW.work_order_id AND w.organization_id=NEW.organization_id
            AND w.location_id=NEW.location_id
            AND a.organization_id=w.organization_id AND a.location_id=w.location_id
            AND b.organization_id=w.organization_id AND b.location_id=w.location_id
            AND b.canceled_at IS NULL
          FOR SHARE OF w,a,b,proof,sku,loc,org
        ) THEN
          RAISE EXCEPTION 'booking material scope unavailable';
        END IF;
        RETURN NEW;
      END $fn$
    """)
    op.execute("""CREATE TRIGGER auto_booking_material_scope
      BEFORE INSERT ON auto_work_order_material_request
      FOR EACH ROW EXECUTE FUNCTION guard_auto_booking_material_request()""")
    op.execute("""CREATE TRIGGER auto_booking_material_immutable
      BEFORE UPDATE OR DELETE ON auto_work_order_material_request
      FOR EACH ROW EXECUTE FUNCTION immutable_auto_stock_event()""")
    op.execute("REVOKE ALL ON auto_work_order_material_request FROM PUBLIC")


def downgrade():
    # Запрет удаления истории после первых booking-intents.
    conn = op.get_bind()
    if conn.execute(text(
        "SELECT 1 FROM auto_work_order_material_request LIMIT 1"
    )).first() or conn.execute(text(
        "SELECT 1 FROM auto_work_order_service WHERE origin='BOOKING' LIMIT 1"
    )).first():
        raise RuntimeError("booking composition exists: restore an isolated backup")
    op.execute("DROP TABLE auto_work_order_material_request")
    op.execute("DROP FUNCTION guard_auto_booking_material_request()")
    op.execute("ALTER TABLE auto_work_order_service DROP COLUMN origin")
