"""Mark immutable initial appointment service choices separately from staff work.

Revision ID: 0097_auto_booking_service_origin
Revises: 0096_auto_outbox_supersession

Existing selections default STAFF: historical provenance is unknown.
"""
from alembic import op
from sqlalchemy import text

revision = "0097_auto_booking_service_origin"
down_revision = "0096_auto_outbox_supersession"
branch_labels = None
depends_on = None

def upgrade():
    op.execute("""ALTER TABLE auto_work_order_service ADD COLUMN selection_origin
      text NOT NULL DEFAULT 'STAFF'
      CONSTRAINT auto_work_order_service_selection_origin_check
      CHECK (selection_origin IN ('STAFF','BOOKING'))""")

def downgrade():
    if op.get_bind().execute(text(
        "SELECT 1 FROM auto_work_order_service WHERE selection_origin='BOOKING' LIMIT 1"
    )).first() is not None:
        raise RuntimeError("booking selections exist: restore isolated backup")
    op.execute("ALTER TABLE auto_work_order_service DROP COLUMN selection_origin")
