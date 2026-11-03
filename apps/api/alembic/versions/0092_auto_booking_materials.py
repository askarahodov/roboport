"""Разделение товаров и расходных материалов при первичной записи (synthetic).

Revision ID: 0092_auto_booking_materials
Revises: 0091_auto_booking_stock_guard

Хранит неизменяемый состав выбранных SKU и ссылку на реальный RESERVE ledger.
Никаких денежных сумм, автоматических списаний или реальных заказов.
"""
from alembic import op
from sqlalchemy import text

revision = "0092_auto_booking_materials"
down_revision = "0091_auto_booking_stock_guard"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
      CREATE TABLE auto_appointment_material_selection (
        stock_event_id uuid PRIMARY KEY
          REFERENCES auto_work_order_stock_event(stock_event_id),
        work_order_id uuid NOT NULL REFERENCES auto_work_order(id),
        sku_id uuid NOT NULL REFERENCES auto_stock_sku(id),
        item_kind text NOT NULL CHECK (item_kind IN ('PART','CONSUMABLE')),
        quantity bigint NOT NULL CHECK (quantity BETWEEN 1 AND 1000000000),
        created_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE (work_order_id,sku_id)
      )
    """)
    op.execute("""
      CREATE FUNCTION guard_auto_appointment_material_selection()
      RETURNS trigger LANGUAGE plpgsql AS $fn$
      BEGIN
        IF NOT EXISTS (
          SELECT 1 FROM auto_work_order_stock_event w
          JOIN auto_stock_event e ON e.id=w.stock_event_id
          WHERE w.stock_event_id=NEW.stock_event_id
            AND w.work_order_id=NEW.work_order_id
            AND w.sku_id=NEW.sku_id
            AND w.action='RESERVE' AND e.action='RESERVE'
            AND w.quantity=NEW.quantity AND e.quantity=NEW.quantity
            AND e.sku_id=NEW.sku_id
        ) THEN
          RAISE EXCEPTION 'appointment material must match stock reserve';
        END IF;
        RETURN NEW;
      END $fn$
    """)
    op.execute("""
      CREATE TRIGGER auto_appointment_material_selection_guard
      BEFORE INSERT ON auto_appointment_material_selection
      FOR EACH ROW EXECUTE FUNCTION guard_auto_appointment_material_selection()
    """)
    op.execute("""
      CREATE TRIGGER auto_appointment_material_selection_immutable
      BEFORE UPDATE OR DELETE ON auto_appointment_material_selection
      FOR EACH ROW EXECUTE FUNCTION immutable_auto_stock_event()
    """)


def downgrade():
    # Downgrade не должен стирать неизменяемые связи с резервом.
    present = op.get_bind().execute(text(
        "SELECT 1 FROM auto_appointment_material_selection LIMIT 1"
    )).first()
    if present is not None:
        raise RuntimeError("booking material receipts exist: use isolated restore")
    op.execute("DROP TABLE auto_appointment_material_selection")
    op.execute("DROP FUNCTION guard_auto_appointment_material_selection()")
