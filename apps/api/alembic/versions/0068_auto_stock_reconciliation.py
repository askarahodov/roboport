"""Read-only stock reconciliation of physical, reserved and placed inventory.

Revision ID: 0068_auto_stock_reconciliation
Revises: 0067_auto_handoff_return_guard
"""
from alembic import op

revision="0068_auto_stock_reconciliation"
down_revision="0067_auto_handoff_return_guard"
branch_labels=None
depends_on=None


def upgrade():
    op.execute("""CREATE VIEW auto_stock_reconciliation AS
      SELECT s.id AS sku_id,s.organization_id,s.location_id,s.article,
        b.on_hand,b.reserved,
        COALESCE(SUM(l.allocated),0)::bigint AS lot_allocated,
        (b.on_hand-COALESCE(SUM(l.allocated),0))::bigint AS unallocated,
        (b.on_hand-b.reserved)::bigint AS available,
        (b.on_hand<0 OR b.reserved<0 OR b.reserved>b.on_hand
          OR COALESCE(SUM(l.allocated),0)<0
          OR COALESCE(SUM(l.allocated),0)>b.on_hand) AS discrepancy
      FROM auto_stock_sku s
      JOIN auto_stock_balance b ON b.sku_id=s.id
      LEFT JOIN auto_stock_lot_balance l ON l.sku_id=s.id
      GROUP BY s.id,s.organization_id,s.location_id,s.article,
               b.on_hand,b.reserved""")
    op.execute("REVOKE ALL ON auto_stock_reconciliation FROM PUBLIC")


def downgrade():
    op.execute("DROP VIEW auto_stock_reconciliation")
