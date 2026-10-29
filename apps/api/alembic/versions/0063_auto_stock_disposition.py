"""Audited physical lot write-off and return-to-supplier disposition.

Revision ID: 0063_auto_stock_disposition
Revises: 0062_auto_interbranch_transfer
"""
from alembic import op

revision="0063_auto_stock_disposition"
down_revision="0062_auto_interbranch_transfer"
branch_labels=None
depends_on=None


def upgrade():
    op.execute("ALTER TABLE auto_stock_event DROP CONSTRAINT auto_stock_event_action_check")
    op.execute("""ALTER TABLE auto_stock_event ADD CONSTRAINT auto_stock_event_action_check CHECK(
      action IN ('RECEIPT','RESERVE','RELEASE','ISSUE','RETURN','COUNT_GAIN','COUNT_LOSS',
                 'TRANSFER_IN','TRANSFER_OUT','WRITE_OFF','SUPPLIER_RETURN'))""")
    op.execute("""CREATE OR REPLACE VIEW auto_stock_balance AS
      SELECT sku.id sku_id,sku.organization_id,sku.location_id,
        COALESCE(SUM(CASE WHEN e.action IN ('RECEIPT','RETURN','COUNT_GAIN','TRANSFER_IN') THEN e.quantity
          WHEN e.action IN ('ISSUE','COUNT_LOSS','TRANSFER_OUT','WRITE_OFF','SUPPLIER_RETURN') THEN -e.quantity ELSE 0 END),0)::bigint on_hand,
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
          RAISE EXCEPTION 'foreign stock scope'; END IF;
        SELECT on_hand,reserved INTO stock FROM auto_stock_balance WHERE sku_id=NEW.sku_id;
        IF (NEW.action='RESERVE' AND stock.on_hand-stock.reserved<NEW.quantity)
           OR (NEW.action IN ('RELEASE','ISSUE') AND stock.reserved<NEW.quantity)
           OR (NEW.action IN ('COUNT_LOSS','TRANSFER_OUT','WRITE_OFF','SUPPLIER_RETURN')
               AND stock.on_hand-stock.reserved<NEW.quantity) THEN
          RAISE EXCEPTION 'insufficient physical or reserved stock'; END IF;
        IF NEW.action IN ('ISSUE','COUNT_LOSS','TRANSFER_OUT','WRITE_OFF','SUPPLIER_RETURN') THEN
          SELECT COALESCE(SUM(lb.allocated),0)::bigint INTO allocated_total
            FROM auto_stock_lot_balance lb WHERE lb.sku_id=NEW.sku_id;
          IF stock.on_hand-allocated_total<NEW.quantity THEN
            RAISE EXCEPTION 'physical stock is allocated to lots'; END IF;
        END IF;
        RETURN NEW;
      END $fn$""")
    op.execute("""CREATE TABLE auto_stock_lot_disposition(
      id uuid PRIMARY KEY,
      request_id uuid NOT NULL UNIQUE,
      lot_id uuid NOT NULL REFERENCES auto_stock_lot(id),
      sku_id uuid NOT NULL REFERENCES auto_stock_sku(id),
      action text NOT NULL CHECK(action IN ('WRITE_OFF','SUPPLIER_RETURN')),
      quantity bigint NOT NULL CHECK(quantity BETWEEN 1 AND 1000000000),
      reason text NOT NULL CHECK(length(btrim(reason)) BETWEEN 3 AND 240),
      supplier_reference text,
      lot_debit_id uuid NOT NULL UNIQUE REFERENCES auto_stock_lot_allocation(id),
      stock_event_id uuid NOT NULL UNIQUE REFERENCES auto_stock_event(id),
      created_at timestamptz NOT NULL DEFAULT now(),
      CHECK((action='SUPPLIER_RETURN' AND supplier_reference IS NOT NULL
          AND length(btrim(supplier_reference)) BETWEEN 3 AND 160)
         OR (action='WRITE_OFF' AND supplier_reference IS NULL))
    )""")
    op.execute("""CREATE TRIGGER auto_stock_lot_disposition_immutable
      BEFORE UPDATE OR DELETE ON auto_stock_lot_disposition
      FOR EACH ROW EXECUTE FUNCTION immutable_auto_stock_event()""")
    op.execute("""CREATE FUNCTION check_auto_stock_lot_disposition()
      RETURNS trigger LANGUAGE plpgsql AS $fn$
      BEGIN
        IF NOT EXISTS(
          SELECT 1 FROM auto_stock_lot_allocation a
          JOIN auto_stock_event e ON e.id=NEW.stock_event_id
          JOIN auto_stock_lot l ON l.id=NEW.lot_id
          WHERE a.id=NEW.lot_debit_id AND a.lot_id=NEW.lot_id
            AND a.sku_id=NEW.sku_id AND a.action='DEALLOCATE'
            AND a.quantity=NEW.quantity
            AND e.sku_id=NEW.sku_id AND e.action=NEW.action
            AND e.quantity=NEW.quantity
            AND e.organization_id=l.organization_id AND e.location_id=l.location_id
            AND l.sku_id=NEW.sku_id
        ) THEN RAISE EXCEPTION 'unpaired warehouse disposition'; END IF;
        RETURN NULL;
      END $fn$""")
    op.execute("""CREATE CONSTRAINT TRIGGER auto_stock_lot_disposition_pair
      AFTER INSERT ON auto_stock_lot_disposition
      DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
      EXECUTE FUNCTION check_auto_stock_lot_disposition()""")
    op.execute("""CREATE FUNCTION record_auto_stock_lot_disposition(
      p_lot uuid,p_request uuid,p_action text,p_quantity bigint,
      p_reason text,p_supplier_reference text
    ) RETURNS uuid LANGUAGE plpgsql AS $fn$
    DECLARE lot_row record; prior record; bal record; debit uuid; physical uuid; result_id uuid;
    BEGIN
      IF p_lot IS NULL OR p_request IS NULL
         OR p_action IS NULL OR p_action NOT IN ('WRITE_OFF','SUPPLIER_RETURN')
         OR p_quantity IS NULL OR p_quantity<1 OR p_quantity>1000000000
         OR p_reason IS NULL OR length(btrim(p_reason)) NOT BETWEEN 3 AND 240
         OR (p_action='SUPPLIER_RETURN' AND (p_supplier_reference IS NULL
              OR length(btrim(p_supplier_reference)) NOT BETWEEN 3 AND 160))
         OR (p_action='WRITE_OFF' AND p_supplier_reference IS NOT NULL) THEN
        RAISE EXCEPTION 'invalid stock disposition'; END IF;
      SELECT l.sku_id,l.organization_id,l.location_id INTO lot_row
        FROM auto_stock_lot l
        JOIN auto_service_location loc ON loc.id=l.location_id
         AND loc.organization_id=l.organization_id
        JOIN auto_organization org ON org.id=l.organization_id
        WHERE l.id=p_lot AND loc.disabled_at IS NULL AND org.disabled_at IS NULL;
      IF NOT FOUND THEN RAISE EXCEPTION 'lot unavailable'; END IF;
      PERFORM 1 FROM auto_stock_sku WHERE id=lot_row.sku_id FOR UPDATE;
      SELECT * INTO prior FROM auto_stock_lot_disposition WHERE request_id=p_request;
      IF FOUND THEN
        IF prior.lot_id=p_lot AND prior.action=p_action AND prior.quantity=p_quantity
          AND prior.reason=p_reason
          AND prior.supplier_reference IS NOT DISTINCT FROM p_supplier_reference THEN
          RETURN prior.id; END IF;
        RAISE EXCEPTION 'disposition replay mismatch';
      END IF;
      SELECT on_hand,reserved INTO bal FROM auto_stock_balance WHERE sku_id=lot_row.sku_id;
      IF bal.on_hand-bal.reserved<p_quantity OR
         (SELECT allocated FROM auto_stock_lot_balance WHERE lot_id=p_lot)<p_quantity THEN
        RAISE EXCEPTION 'insufficient unreserved lot stock'; END IF;
      debit:=record_auto_stock_lot_allocation(p_lot,gen_random_uuid(),'DEALLOCATE',p_quantity);
      physical:=gen_random_uuid();
      INSERT INTO auto_stock_event(id,request_id,sku_id,organization_id,location_id,action,quantity)
        VALUES(physical,gen_random_uuid(),lot_row.sku_id,lot_row.organization_id,
               lot_row.location_id,p_action,p_quantity);
      result_id:=gen_random_uuid();
      INSERT INTO auto_stock_lot_disposition(
        id,request_id,lot_id,sku_id,action,quantity,reason,
        supplier_reference,lot_debit_id,stock_event_id)
        VALUES(result_id,p_request,p_lot,lot_row.sku_id,p_action,p_quantity,
               p_reason,p_supplier_reference,debit,physical);
      RETURN result_id;
    END $fn$""")
    op.execute("""REVOKE ALL ON FUNCTION record_auto_stock_lot_disposition(uuid,uuid,text,bigint,text,text) FROM PUBLIC""")


def downgrade():
    op.execute("DROP FUNCTION record_auto_stock_lot_disposition(uuid,uuid,text,bigint,text,text)")
    op.execute("DROP TABLE auto_stock_lot_disposition")
    op.execute("DROP FUNCTION check_auto_stock_lot_disposition()")
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
           OR (NEW.action IN ('COUNT_LOSS','TRANSFER_OUT') AND stock.on_hand-stock.reserved<NEW.quantity)
        THEN RAISE EXCEPTION 'insufficient physical or reserved stock'; END IF;
        IF NEW.action IN ('ISSUE','COUNT_LOSS','TRANSFER_OUT') THEN
          SELECT COALESCE(SUM(lb.allocated),0)::bigint INTO allocated_total FROM auto_stock_lot_balance lb WHERE lb.sku_id=NEW.sku_id;
          IF stock.on_hand-allocated_total<NEW.quantity THEN RAISE EXCEPTION 'physical stock is allocated to lots'; END IF;
        END IF; RETURN NEW;
      END $fn$""")
    op.execute("""CREATE OR REPLACE VIEW auto_stock_balance AS
      SELECT sku.id sku_id,sku.organization_id,sku.location_id,
        COALESCE(SUM(CASE WHEN e.action IN ('RECEIPT','RETURN','COUNT_GAIN','TRANSFER_IN') THEN e.quantity
          WHEN e.action IN ('ISSUE','COUNT_LOSS','TRANSFER_OUT') THEN -e.quantity ELSE 0 END),0)::bigint on_hand,
        COALESCE(SUM(CASE WHEN e.action='RESERVE' THEN e.quantity
          WHEN e.action IN ('RELEASE','ISSUE') THEN -e.quantity ELSE 0 END),0)::bigint reserved
      FROM auto_stock_sku sku LEFT JOIN auto_stock_event e ON e.sku_id=sku.id
      GROUP BY sku.id,sku.organization_id,sku.location_id""")
    op.execute("ALTER TABLE auto_stock_event DROP CONSTRAINT auto_stock_event_action_check")
    op.execute("""ALTER TABLE auto_stock_event ADD CONSTRAINT auto_stock_event_action_check
      CHECK(action IN ('RECEIPT','RESERVE','RELEASE','ISSUE','RETURN',
                       'COUNT_GAIN','COUNT_LOSS','TRANSFER_IN','TRANSFER_OUT'))""")
