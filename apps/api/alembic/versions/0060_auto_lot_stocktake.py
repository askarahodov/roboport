"""Warehouse lot stocktake snapshots and atomic adjustments.

Revision ID: 0060_auto_lot_stocktake
Revises: 0059_auto_lot_work_order_return
"""
from alembic import op

revision="0060_auto_lot_stocktake"
down_revision="0059_auto_lot_work_order_return"
branch_labels=None
depends_on=None


def upgrade():
    op.execute("""ALTER TABLE auto_stock_event DROP CONSTRAINT auto_stock_event_action_check""")
    op.execute("""ALTER TABLE auto_stock_event ADD CONSTRAINT auto_stock_event_action_check CHECK
      (action IN ('RECEIPT','RESERVE','RELEASE','ISSUE','RETURN','COUNT_GAIN','COUNT_LOSS'))""")
    op.execute("""CREATE OR REPLACE VIEW auto_stock_balance AS
      SELECT sku.id sku_id,sku.organization_id,sku.location_id,
        COALESCE(SUM(CASE WHEN e.action IN ('RECEIPT','RETURN','COUNT_GAIN') THEN e.quantity
          WHEN e.action IN ('ISSUE','COUNT_LOSS') THEN -e.quantity ELSE 0 END),0)::bigint on_hand,
        COALESCE(SUM(CASE WHEN e.action='RESERVE' THEN e.quantity
          WHEN e.action IN ('RELEASE','ISSUE') THEN -e.quantity ELSE 0 END),0)::bigint reserved
      FROM auto_stock_sku sku LEFT JOIN auto_stock_event e ON e.sku_id=sku.id
      GROUP BY sku.id,sku.organization_id,sku.location_id""")
    op.execute("""CREATE OR REPLACE FUNCTION guard_auto_stock_event_insert()
      RETURNS trigger LANGUAGE plpgsql AS $fn$
      DECLARE scope record; stock record; allocated_total bigint;
      BEGIN
        SELECT organization_id,location_id INTO scope FROM auto_stock_sku
          WHERE id=NEW.sku_id FOR UPDATE;
        IF NOT FOUND OR (NEW.organization_id,NEW.location_id)
           IS DISTINCT FROM (scope.organization_id,scope.location_id) THEN
          RAISE EXCEPTION 'foreign stock scope';
        END IF;
        SELECT on_hand,reserved INTO stock FROM auto_stock_balance WHERE sku_id=NEW.sku_id;
        IF (NEW.action='RESERVE' AND stock.on_hand-stock.reserved<NEW.quantity)
           OR (NEW.action IN ('RELEASE','ISSUE') AND stock.reserved<NEW.quantity)
           OR (NEW.action='COUNT_LOSS' AND stock.on_hand-stock.reserved<NEW.quantity) THEN
          RAISE EXCEPTION 'insufficient physical or reserved stock';
        END IF;
        IF NEW.action IN ('ISSUE','COUNT_LOSS') THEN
          SELECT COALESCE(SUM(lb.allocated),0)::bigint INTO allocated_total
            FROM auto_stock_lot_balance lb WHERE lb.sku_id=NEW.sku_id;
          IF stock.on_hand-allocated_total<NEW.quantity THEN
            RAISE EXCEPTION 'physical stock is allocated to lots';
          END IF;
        END IF;
        RETURN NEW;
      END $fn$""")
    op.execute("""CREATE TABLE auto_stock_lot_count (
      id uuid PRIMARY KEY, request_id uuid NOT NULL UNIQUE,
      lot_id uuid NOT NULL REFERENCES auto_stock_lot(id),
      sku_id uuid NOT NULL REFERENCES auto_stock_sku(id),
      expected bigint NOT NULL CHECK(expected>=0),
      actual bigint NOT NULL CHECK(actual>=0 AND actual<=1000000000),
      reason text NOT NULL CHECK(length(btrim(reason)) BETWEEN 3 AND 240),
      created_at timestamptz NOT NULL DEFAULT now()
    )""")
    op.execute("""CREATE TABLE auto_stock_lot_count_approval (
      id uuid PRIMARY KEY, request_id uuid NOT NULL UNIQUE,
      count_id uuid NOT NULL UNIQUE REFERENCES auto_stock_lot_count(id),
      applied_delta bigint NOT NULL,
      lot_event_id uuid REFERENCES auto_stock_lot_allocation(id),
      stock_event_id uuid REFERENCES auto_stock_event(id),
      created_at timestamptz NOT NULL DEFAULT now(),
      CHECK((applied_delta=0 AND lot_event_id IS NULL AND stock_event_id IS NULL)
        OR (applied_delta<>0 AND lot_event_id IS NOT NULL AND stock_event_id IS NOT NULL))
    )""")
    op.execute("""CREATE TRIGGER auto_stock_lot_count_immutable BEFORE UPDATE OR DELETE
      ON auto_stock_lot_count FOR EACH ROW EXECUTE FUNCTION immutable_auto_stock_event()""")
    op.execute("""CREATE TRIGGER auto_stock_lot_count_approval_immutable BEFORE UPDATE OR DELETE
      ON auto_stock_lot_count_approval FOR EACH ROW EXECUTE FUNCTION immutable_auto_stock_event()""")
    op.execute("""CREATE FUNCTION record_auto_stock_lot_count(
      p_lot uuid,p_request uuid,p_actual bigint,p_reason text
    ) RETURNS uuid LANGUAGE plpgsql AS $fn$
    DECLARE scope record; previous record; expected_qty bigint; result_id uuid;
    BEGIN
      IF p_lot IS NULL OR p_request IS NULL OR p_actual IS NULL
         OR p_actual<0 OR p_actual>1000000000
         OR p_reason IS NULL OR length(btrim(p_reason)) NOT BETWEEN 3 AND 240 THEN
        RAISE EXCEPTION 'invalid stocktake count';
      END IF;
      SELECT l.sku_id INTO scope FROM auto_stock_lot l
        JOIN auto_service_location loc ON loc.id=l.location_id
          AND loc.organization_id=l.organization_id
        JOIN auto_organization org ON org.id=l.organization_id
        WHERE l.id=p_lot AND loc.disabled_at IS NULL AND org.disabled_at IS NULL;
      IF NOT FOUND THEN RAISE EXCEPTION 'lot unavailable'; END IF;
      PERFORM 1 FROM auto_stock_sku WHERE id=scope.sku_id FOR UPDATE;
      SELECT * INTO previous FROM auto_stock_lot_count WHERE request_id=p_request;
      IF FOUND THEN
        IF previous.lot_id=p_lot AND previous.actual=p_actual AND previous.reason=p_reason
          THEN RETURN previous.id; END IF;
        RAISE EXCEPTION 'count request mismatch';
      END IF;
      SELECT allocated INTO expected_qty FROM auto_stock_lot_balance WHERE lot_id=p_lot;
      result_id:=gen_random_uuid();
      INSERT INTO auto_stock_lot_count(id,request_id,lot_id,sku_id,expected,actual,reason)
        VALUES(result_id,p_request,p_lot,scope.sku_id,expected_qty,p_actual,p_reason);
      RETURN result_id;
    END $fn$""")
    op.execute("""CREATE FUNCTION approve_auto_stock_lot_count(
      p_count uuid,p_request uuid
    ) RETURNS uuid LANGUAGE plpgsql AS $fn$
    DECLARE cnt record; previous record; qty bigint; delta bigint;
            event_id uuid; lot_event uuid; approval_id uuid; physical record; allocated_total bigint;
    BEGIN
      IF p_count IS NULL OR p_request IS NULL THEN RAISE EXCEPTION 'invalid approval'; END IF;
      SELECT c.*,l.organization_id,l.location_id INTO cnt FROM auto_stock_lot_count c
        JOIN auto_stock_lot l ON l.id=c.lot_id
        JOIN auto_service_location loc ON loc.id=l.location_id
          AND loc.organization_id=l.organization_id
        JOIN auto_organization org ON org.id=l.organization_id
        WHERE c.id=p_count AND loc.disabled_at IS NULL AND org.disabled_at IS NULL;
      IF NOT FOUND THEN RAISE EXCEPTION 'count unavailable'; END IF;
      PERFORM 1 FROM auto_stock_sku WHERE id=cnt.sku_id FOR UPDATE;
      SELECT * INTO previous FROM auto_stock_lot_count_approval WHERE request_id=p_request;
      IF FOUND THEN
        IF previous.count_id=p_count THEN RETURN previous.id; END IF;
        RAISE EXCEPTION 'approval request mismatch';
      END IF;
      IF EXISTS(SELECT 1 FROM auto_stock_lot_count_approval WHERE count_id=p_count)
        THEN RAISE EXCEPTION 'count already approved'; END IF;
      SELECT lb.allocated INTO qty FROM auto_stock_lot_balance lb WHERE lb.lot_id=cnt.lot_id;
      IF qty<>cnt.expected THEN RAISE EXCEPTION 'stale lot count'; END IF;
      delta:=cnt.actual-cnt.expected;
      event_id:=NULL; lot_event:=NULL;
      IF delta<0 THEN
        SELECT on_hand,reserved INTO physical FROM auto_stock_balance WHERE sku_id=cnt.sku_id;
        SELECT COALESCE(SUM(lb.allocated),0)::bigint INTO allocated_total
          FROM auto_stock_lot_balance lb WHERE lb.sku_id=cnt.sku_id;
        IF physical.on_hand+delta<physical.reserved OR physical.on_hand+delta<allocated_total+delta
          THEN RAISE EXCEPTION 'count loss would violate reserves or allocation'; END IF;
        lot_event:=record_auto_stock_lot_allocation(cnt.lot_id,gen_random_uuid(),'DEALLOCATE',-delta);
        event_id:=gen_random_uuid();
        INSERT INTO auto_stock_event(id,request_id,sku_id,organization_id,location_id,action,quantity)
          VALUES(event_id,gen_random_uuid(),cnt.sku_id,cnt.organization_id,cnt.location_id,'COUNT_LOSS',-delta);
      ELSIF delta>0 THEN
        event_id:=gen_random_uuid();
        INSERT INTO auto_stock_event(id,request_id,sku_id,organization_id,location_id,action,quantity)
          VALUES(event_id,gen_random_uuid(),cnt.sku_id,cnt.organization_id,cnt.location_id,'COUNT_GAIN',delta);
        lot_event:=record_auto_stock_lot_allocation(cnt.lot_id,gen_random_uuid(),'ALLOCATE',delta);
      END IF;
      approval_id:=gen_random_uuid();
      INSERT INTO auto_stock_lot_count_approval
        (id,request_id,count_id,applied_delta,lot_event_id,stock_event_id)
        VALUES(approval_id,p_request,p_count,delta,lot_event,event_id);
      RETURN approval_id;
    END $fn$""")
    op.execute("REVOKE ALL ON FUNCTION record_auto_stock_lot_count(uuid,uuid,bigint,text) FROM PUBLIC")
    op.execute("REVOKE ALL ON FUNCTION approve_auto_stock_lot_count(uuid,uuid) FROM PUBLIC")


def downgrade():
    op.execute("DROP FUNCTION approve_auto_stock_lot_count(uuid,uuid)")
    op.execute("DROP FUNCTION record_auto_stock_lot_count(uuid,uuid,bigint,text)")
    op.execute("DROP TABLE auto_stock_lot_count_approval")
    op.execute("DROP TABLE auto_stock_lot_count")
    op.execute("""CREATE OR REPLACE FUNCTION guard_auto_stock_event_insert()
      RETURNS trigger LANGUAGE plpgsql AS $fn$
      DECLARE scope record; stock record; allocated_total bigint;
      BEGIN
        SELECT organization_id,location_id INTO scope FROM auto_stock_sku WHERE id=NEW.sku_id FOR UPDATE;
        IF NOT FOUND OR (NEW.organization_id,NEW.location_id)
          IS DISTINCT FROM (scope.organization_id,scope.location_id) THEN RAISE EXCEPTION 'foreign stock scope'; END IF;
        SELECT on_hand,reserved INTO stock FROM auto_stock_balance WHERE sku_id=NEW.sku_id;
        IF (NEW.action='RESERVE' AND stock.on_hand-stock.reserved<NEW.quantity)
          OR (NEW.action IN ('RELEASE','ISSUE') AND stock.reserved<NEW.quantity)
          THEN RAISE EXCEPTION 'insufficient stock or reservation'; END IF;
        IF NEW.action='ISSUE' THEN
          SELECT COALESCE(SUM(lb.allocated),0)::bigint INTO allocated_total
            FROM auto_stock_lot_balance lb WHERE lb.sku_id=NEW.sku_id;
          IF stock.on_hand-allocated_total<NEW.quantity THEN RAISE EXCEPTION 'issued quantity is allocated to lots'; END IF;
        END IF;
        RETURN NEW;
      END $fn$""")
    op.execute("""CREATE OR REPLACE VIEW auto_stock_balance AS
      SELECT sku.id sku_id,sku.organization_id,sku.location_id,
        COALESCE(SUM(CASE WHEN e.action IN ('RECEIPT','RETURN') THEN e.quantity
          WHEN e.action='ISSUE' THEN -e.quantity ELSE 0 END),0)::bigint on_hand,
        COALESCE(SUM(CASE WHEN e.action='RESERVE' THEN e.quantity
          WHEN e.action IN ('RELEASE','ISSUE') THEN -e.quantity ELSE 0 END),0)::bigint reserved
      FROM auto_stock_sku sku LEFT JOIN auto_stock_event e ON e.sku_id=sku.id
      GROUP BY sku.id,sku.organization_id,sku.location_id""")
    op.execute("ALTER TABLE auto_stock_event DROP CONSTRAINT auto_stock_event_action_check")
    op.execute("""ALTER TABLE auto_stock_event ADD CONSTRAINT auto_stock_event_action_check
      CHECK(action IN ('RECEIPT','RESERVE','RELEASE','ISSUE','RETURN'))""")
