"""Atomic warehouse interbranch movement with physical and lot-level provenance.

Revision ID: 0062_auto_interbranch_transfer
Revises: 0061_auto_stocktake_dual_control
"""
from alembic import op

revision="0062_auto_interbranch_transfer"
down_revision="0061_auto_stocktake_dual_control"
branch_labels=None
depends_on=None


def upgrade():
    op.execute("""ALTER TABLE auto_stock_event DROP CONSTRAINT auto_stock_event_action_check""")
    op.execute("""ALTER TABLE auto_stock_event ADD CONSTRAINT auto_stock_event_action_check CHECK
      (action IN ('RECEIPT','RESERVE','RELEASE','ISSUE','RETURN',
                  'COUNT_GAIN','COUNT_LOSS','TRANSFER_IN','TRANSFER_OUT'))""")
    op.execute("""CREATE OR REPLACE VIEW auto_stock_balance AS
      SELECT sku.id sku_id,sku.organization_id,sku.location_id,
        COALESCE(SUM(CASE WHEN e.action IN ('RECEIPT','RETURN','COUNT_GAIN','TRANSFER_IN') THEN e.quantity
          WHEN e.action IN ('ISSUE','COUNT_LOSS','TRANSFER_OUT') THEN -e.quantity ELSE 0 END),0)::bigint on_hand,
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
           OR (NEW.action IN ('COUNT_LOSS','TRANSFER_OUT')
               AND stock.on_hand-stock.reserved<NEW.quantity) THEN
          RAISE EXCEPTION 'insufficient physical or reserved stock'; END IF;
        IF NEW.action IN ('ISSUE','COUNT_LOSS','TRANSFER_OUT') THEN
          SELECT COALESCE(SUM(lb.allocated),0)::bigint INTO allocated_total
            FROM auto_stock_lot_balance lb WHERE lb.sku_id=NEW.sku_id;
          IF stock.on_hand-allocated_total<NEW.quantity THEN
            RAISE EXCEPTION 'physical stock is allocated to lots'; END IF;
        END IF;
        RETURN NEW;
      END $fn$""")
    op.execute("""CREATE TABLE auto_stock_interbranch_transfer (
      id uuid PRIMARY KEY,
      request_id uuid NOT NULL UNIQUE,
      organization_id uuid NOT NULL REFERENCES auto_organization(id),
      source_lot_id uuid NOT NULL REFERENCES auto_stock_lot(id),
      destination_lot_id uuid NOT NULL REFERENCES auto_stock_lot(id),
      source_sku_id uuid NOT NULL REFERENCES auto_stock_sku(id),
      destination_sku_id uuid NOT NULL REFERENCES auto_stock_sku(id),
      quantity bigint NOT NULL CHECK (quantity BETWEEN 1 AND 1000000000),
      debit_allocation_id uuid NOT NULL UNIQUE REFERENCES auto_stock_lot_allocation(id),
      credit_allocation_id uuid NOT NULL UNIQUE REFERENCES auto_stock_lot_allocation(id),
      debit_stock_id uuid NOT NULL UNIQUE REFERENCES auto_stock_event(id),
      credit_stock_id uuid NOT NULL UNIQUE REFERENCES auto_stock_event(id),
      created_at timestamptz NOT NULL DEFAULT now(),
      CHECK (source_lot_id<>destination_lot_id),
      CHECK (source_sku_id<>destination_sku_id)
    )""")
    op.execute("""CREATE TRIGGER auto_stock_interbranch_transfer_immutable
      BEFORE UPDATE OR DELETE ON auto_stock_interbranch_transfer
      FOR EACH ROW EXECUTE FUNCTION immutable_auto_stock_event()""")
    op.execute("""CREATE FUNCTION check_auto_stock_interbranch_transfer()
      RETURNS trigger LANGUAGE plpgsql AS $fn$
      BEGIN
        IF NOT EXISTS (
          SELECT 1 FROM auto_stock_lot src
          JOIN auto_stock_lot dst ON dst.id=NEW.destination_lot_id
          JOIN auto_stock_sku ss ON ss.id=NEW.source_sku_id
          JOIN auto_stock_sku ds ON ds.id=NEW.destination_sku_id
          JOIN auto_stock_lot_allocation da ON da.id=NEW.debit_allocation_id
          JOIN auto_stock_lot_allocation ca ON ca.id=NEW.credit_allocation_id
          JOIN auto_stock_event de ON de.id=NEW.debit_stock_id
          JOIN auto_stock_event ce ON ce.id=NEW.credit_stock_id
          WHERE src.id=NEW.source_lot_id
            AND src.sku_id=ss.id AND dst.sku_id=ds.id
            AND src.organization_id=NEW.organization_id
            AND dst.organization_id=NEW.organization_id
            AND ss.organization_id=NEW.organization_id
            AND ds.organization_id=NEW.organization_id
            AND ss.article=ds.article AND ss.location_id<>ds.location_id
            AND src.location_id=ss.location_id AND dst.location_id=ds.location_id
            AND da.lot_id=src.id AND da.sku_id=ss.id
            AND ca.lot_id=dst.id AND ca.sku_id=ds.id
            AND da.action='DEALLOCATE' AND ca.action='ALLOCATE'
            AND da.quantity=NEW.quantity AND ca.quantity=NEW.quantity
            AND de.sku_id=ss.id AND de.action='TRANSFER_OUT'
            AND ce.sku_id=ds.id AND ce.action='TRANSFER_IN'
            AND de.quantity=NEW.quantity AND ce.quantity=NEW.quantity
        ) THEN RAISE EXCEPTION 'invalid interbranch pairing'; END IF;
        RETURN NULL;
      END $fn$""")
    op.execute("""CREATE CONSTRAINT TRIGGER auto_stock_interbranch_transfer_pair
      AFTER INSERT ON auto_stock_interbranch_transfer
      DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
      EXECUTE FUNCTION check_auto_stock_interbranch_transfer()""")
    op.execute("""CREATE FUNCTION record_auto_stock_interbranch_transfer(
      p_source uuid,p_destination uuid,p_request uuid,p_quantity bigint
    ) RETURNS uuid LANGUAGE plpgsql AS $fn$
    DECLARE s record; d record; prior record; source_balance record;
      debit_lot uuid; credit_lot uuid; debit_stock uuid; credit_stock uuid;
      transfer_id uuid; locked_count integer;
    BEGIN
      IF p_source IS NULL OR p_destination IS NULL OR p_request IS NULL
         OR p_source=p_destination OR p_quantity IS NULL
         OR p_quantity<1 OR p_quantity>1000000000 THEN
        RAISE EXCEPTION 'invalid interbranch transfer'; END IF;
      SELECT l.sku_id,l.organization_id,l.location_id,k.article
        INTO s FROM auto_stock_lot l JOIN auto_stock_sku k ON k.id=l.sku_id
        JOIN auto_service_location loc ON loc.id=l.location_id AND loc.organization_id=l.organization_id
        JOIN auto_organization org ON org.id=l.organization_id
        WHERE l.id=p_source AND loc.disabled_at IS NULL AND org.disabled_at IS NULL;
      IF NOT FOUND THEN RAISE EXCEPTION 'source lot unavailable'; END IF;
      SELECT l.sku_id,l.organization_id,l.location_id,k.article
        INTO d FROM auto_stock_lot l JOIN auto_stock_sku k ON k.id=l.sku_id
        JOIN auto_service_location loc ON loc.id=l.location_id AND loc.organization_id=l.organization_id
        JOIN auto_organization org ON org.id=l.organization_id
        WHERE l.id=p_destination AND loc.disabled_at IS NULL AND org.disabled_at IS NULL;
      IF NOT FOUND OR s.organization_id<>d.organization_id OR s.location_id=d.location_id
         OR s.article<>d.article OR s.sku_id=d.sku_id THEN
        RAISE EXCEPTION 'interbranch scope/article mismatch'; END IF;
      -- Deterministic lock order across all transfers avoids reverse-direction deadlock.
      SELECT count(*) INTO locked_count FROM (
        SELECT id FROM auto_stock_sku WHERE id IN (s.sku_id,d.sku_id)
          ORDER BY id FOR UPDATE
      ) locked;
      IF locked_count<>2 THEN RAISE EXCEPTION 'stock sku unavailable'; END IF;
      SELECT * INTO prior FROM auto_stock_interbranch_transfer WHERE request_id=p_request;
      IF FOUND THEN
        IF prior.source_lot_id=p_source AND prior.destination_lot_id=p_destination
           AND prior.quantity=p_quantity THEN RETURN prior.id; END IF;
        RAISE EXCEPTION 'interbranch request mismatch'; END IF;
      SELECT on_hand,reserved INTO source_balance FROM auto_stock_balance WHERE sku_id=s.sku_id;
      IF source_balance.on_hand-source_balance.reserved<p_quantity
          OR (SELECT allocated FROM auto_stock_lot_balance WHERE lot_id=p_source)<p_quantity THEN
        RAISE EXCEPTION 'interbranch quantity unavailable'; END IF;
      debit_lot:=record_auto_stock_lot_allocation(p_source,gen_random_uuid(),'DEALLOCATE',p_quantity);
      debit_stock:=gen_random_uuid();
      INSERT INTO auto_stock_event
        (id,request_id,sku_id,organization_id,location_id,action,quantity)
        VALUES(debit_stock,gen_random_uuid(),s.sku_id,s.organization_id,s.location_id,'TRANSFER_OUT',p_quantity);
      credit_stock:=gen_random_uuid();
      INSERT INTO auto_stock_event
        (id,request_id,sku_id,organization_id,location_id,action,quantity)
        VALUES(credit_stock,gen_random_uuid(),d.sku_id,d.organization_id,d.location_id,'TRANSFER_IN',p_quantity);
      credit_lot:=record_auto_stock_lot_allocation(p_destination,gen_random_uuid(),'ALLOCATE',p_quantity);
      transfer_id:=gen_random_uuid();
      INSERT INTO auto_stock_interbranch_transfer
        (id,request_id,organization_id,source_lot_id,destination_lot_id,
         source_sku_id,destination_sku_id,quantity,debit_allocation_id,
         credit_allocation_id,debit_stock_id,credit_stock_id)
        VALUES(transfer_id,p_request,s.organization_id,p_source,p_destination,
               s.sku_id,d.sku_id,p_quantity,debit_lot,credit_lot,debit_stock,credit_stock);
      RETURN transfer_id;
    END $fn$""")
    op.execute("""REVOKE ALL ON FUNCTION record_auto_stock_interbranch_transfer(uuid,uuid,uuid,bigint) FROM PUBLIC""")


def downgrade():
    op.execute("DROP FUNCTION record_auto_stock_interbranch_transfer(uuid,uuid,uuid,bigint)")
    op.execute("DROP TABLE auto_stock_interbranch_transfer")
    op.execute("DROP FUNCTION check_auto_stock_interbranch_transfer()")
    op.execute("""CREATE OR REPLACE FUNCTION guard_auto_stock_event_insert()
      RETURNS trigger LANGUAGE plpgsql AS $fn$
      DECLARE scope record; stock record; allocated_total bigint;
      BEGIN
        SELECT organization_id,location_id INTO scope FROM auto_stock_sku WHERE id=NEW.sku_id FOR UPDATE;
        IF NOT FOUND OR (NEW.organization_id,NEW.location_id)
            IS DISTINCT FROM (scope.organization_id,scope.location_id) THEN
          RAISE EXCEPTION 'foreign stock scope'; END IF;
        SELECT on_hand,reserved INTO stock FROM auto_stock_balance WHERE sku_id=NEW.sku_id;
        IF (NEW.action='RESERVE' AND stock.on_hand-stock.reserved<NEW.quantity)
           OR (NEW.action IN ('RELEASE','ISSUE') AND stock.reserved<NEW.quantity)
           OR (NEW.action='COUNT_LOSS' AND stock.on_hand-stock.reserved<NEW.quantity) THEN
          RAISE EXCEPTION 'insufficient physical or reserved stock'; END IF;
        IF NEW.action IN ('ISSUE','COUNT_LOSS') THEN
          SELECT COALESCE(SUM(lb.allocated),0)::bigint INTO allocated_total FROM auto_stock_lot_balance lb WHERE lb.sku_id=NEW.sku_id;
          IF stock.on_hand-allocated_total<NEW.quantity THEN
            RAISE EXCEPTION 'physical stock is allocated to lots'; END IF;
        END IF;
        RETURN NEW;
      END $fn$""")
    op.execute("""CREATE OR REPLACE VIEW auto_stock_balance AS
      SELECT sku.id sku_id,sku.organization_id,sku.location_id,
        COALESCE(SUM(CASE WHEN e.action IN ('RECEIPT','RETURN','COUNT_GAIN') THEN e.quantity
          WHEN e.action IN ('ISSUE','COUNT_LOSS') THEN -e.quantity ELSE 0 END),0)::bigint on_hand,
        COALESCE(SUM(CASE WHEN e.action='RESERVE' THEN e.quantity
          WHEN e.action IN ('RELEASE','ISSUE') THEN -e.quantity ELSE 0 END),0)::bigint reserved
      FROM auto_stock_sku sku LEFT JOIN auto_stock_event e ON e.sku_id=sku.id
      GROUP BY sku.id,sku.organization_id,sku.location_id""")
    op.execute("ALTER TABLE auto_stock_event DROP CONSTRAINT auto_stock_event_action_check")
    op.execute("""ALTER TABLE auto_stock_event ADD CONSTRAINT auto_stock_event_action_check
      CHECK (action IN ('RECEIPT','RESERVE','RELEASE','ISSUE','RETURN','COUNT_GAIN','COUNT_LOSS'))""")
