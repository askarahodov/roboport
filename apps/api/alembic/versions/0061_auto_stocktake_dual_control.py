"""Independent stocktake recount and approval provenance.

Revision ID: 0061_auto_stocktake_dual_control
Revises: 0060_auto_lot_stocktake
"""
from alembic import op

revision = "0061_auto_stocktake_dual_control"
down_revision = "0060_auto_lot_stocktake"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""ALTER TABLE auto_stock_lot_count
      ADD COLUMN counted_by uuid NOT NULL REFERENCES app_user(id)""")
    op.execute("""ALTER TABLE auto_stock_lot_count_approval
      ADD COLUMN approved_by uuid NOT NULL REFERENCES app_user(id)""")
    op.execute("""
      CREATE FUNCTION guard_auto_stocktake_actor()
      RETURNS trigger LANGUAGE plpgsql AS $fn$
      DECLARE actor_id uuid; original_counter uuid;
      BEGIN
        actor_id:=NULLIF(current_setting('roboport.stocktake_actor',true),'')::uuid;
        IF actor_id IS NULL THEN RAISE EXCEPTION 'stocktake actor required'; END IF;
        IF TG_TABLE_NAME='auto_stock_lot_count' THEN
          NEW.counted_by:=actor_id;
        ELSE
          SELECT counted_by INTO original_counter
            FROM auto_stock_lot_count WHERE id=NEW.count_id FOR SHARE;
          IF NOT FOUND OR original_counter=actor_id THEN
            RAISE EXCEPTION 'independent stocktake approver required';
          END IF;
          NEW.approved_by:=actor_id;
        END IF;
        RETURN NEW;
      END $fn$
    """)
    op.execute("""CREATE TRIGGER auto_stock_lot_count_actor
      BEFORE INSERT ON auto_stock_lot_count
      FOR EACH ROW EXECUTE FUNCTION guard_auto_stocktake_actor()""")
    op.execute("""CREATE TRIGGER auto_stock_lot_count_approval_actor
      BEFORE INSERT ON auto_stock_lot_count_approval
      FOR EACH ROW EXECUTE FUNCTION guard_auto_stocktake_actor()""")


def downgrade():
    # Disposable synthetic DB only; removes historical actor provenance.
    op.execute("DROP TRIGGER auto_stock_lot_count_approval_actor ON auto_stock_lot_count_approval")
    op.execute("DROP TRIGGER auto_stock_lot_count_actor ON auto_stock_lot_count")
    op.execute("DROP FUNCTION guard_auto_stocktake_actor()")
    op.execute("ALTER TABLE auto_stock_lot_count_approval DROP COLUMN approved_by")
    op.execute("ALTER TABLE auto_stock_lot_count DROP COLUMN counted_by")
