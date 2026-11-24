"""Неизменяемое подтверждение межфилиального перемещения сотрудником."""
from alembic import op
from sqlalchemy import text

revision="0137_auto_transfer_receipt"
down_revision="0136_auto_service_recommend"
branch_labels=None
depends_on=None


def upgrade():
    op.execute("""
      CREATE TABLE auto_stock_transfer_receipt (
        transfer_id uuid PRIMARY KEY REFERENCES auto_stock_interbranch_transfer(id),
        actor_id uuid NOT NULL REFERENCES app_user(id),
        created_at timestamptz NOT NULL DEFAULT clock_timestamp()
      )
    """)
    op.execute("""
      CREATE TRIGGER auto_stock_transfer_receipt_immutable
      BEFORE UPDATE OR DELETE ON auto_stock_transfer_receipt
      FOR EACH ROW EXECUTE FUNCTION immutable_auto_stock_event()
    """)
    op.execute("""
      CREATE TABLE auto_stock_local_transfer_receipt (
        transfer_id uuid PRIMARY KEY REFERENCES auto_stock_lot_transfer(id),
        actor_id uuid NOT NULL REFERENCES app_user(id),
        created_at timestamptz NOT NULL DEFAULT clock_timestamp()
      )
    """)
    op.execute("""
      CREATE TRIGGER auto_stock_local_transfer_receipt_immutable
      BEFORE UPDATE OR DELETE ON auto_stock_local_transfer_receipt
      FOR EACH ROW EXECUTE FUNCTION immutable_auto_stock_event()
    """)
    op.execute("REVOKE ALL ON auto_stock_transfer_receipt,auto_stock_local_transfer_receipt FROM PUBLIC")


def downgrade():
    if op.get_bind().execute(text("SELECT 1 FROM auto_stock_transfer_receipt UNION ALL SELECT 1 FROM auto_stock_local_transfer_receipt LIMIT 1")).first():
        raise RuntimeError("Сохраните подтверждения перемещений: требуется восстановление резервной копии")
    op.execute("DROP TABLE auto_stock_local_transfer_receipt")
    op.execute("DROP TABLE auto_stock_transfer_receipt")
