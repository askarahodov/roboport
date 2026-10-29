"""Two-person stock reclassification with four immutable balanced ledger legs.

Revision ID: 0070_auto_stock_reclassification
Revises: 0069_auto_stock_serial
"""
from alembic import op

revision = "0070_auto_stock_reclassification"
down_revision = "0069_auto_stock_serial"
branch_labels = None
depends_on = None


def _stock_guard(include_reclass: bool):
    out = "'COUNT_LOSS','TRANSFER_OUT','WRITE_OFF','SUPPLIER_RETURN'"
    if include_reclass:
        out += ",'RECLASS_OUT'"
    op.execute(f"""CREATE OR REPLACE FUNCTION guard_auto_stock_event_insert()
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
           OR (NEW.action IN ({out})
              AND stock.on_hand-stock.reserved<NEW.quantity) THEN
          RAISE EXCEPTION 'insufficient physical or reserved stock'; END IF;
        IF NEW.action IN ('ISSUE',{out}) THEN
          SELECT COALESCE(SUM(lb.allocated),0)::bigint INTO allocated_total
          FROM auto_stock_lot_balance lb WHERE lb.sku_id=NEW.sku_id;
          IF stock.on_hand-allocated_total<NEW.quantity THEN
            RAISE EXCEPTION 'physical stock allocated to lots'; END IF;
        END IF;
        RETURN NEW;
      END $fn$""")


def _stock_view(include_reclass: bool):
    ins = ",'RECLASS_IN'" if include_reclass else ""
    outs = ",'RECLASS_OUT'" if include_reclass else ""
    op.execute(f"""CREATE OR REPLACE VIEW auto_stock_balance AS
      SELECT sku.id sku_id,sku.organization_id,sku.location_id,
        COALESCE(SUM(CASE WHEN e.action IN
          ('RECEIPT','RETURN','COUNT_GAIN','TRANSFER_IN'{ins}) THEN e.quantity
          WHEN e.action IN
          ('ISSUE','COUNT_LOSS','TRANSFER_OUT','WRITE_OFF','SUPPLIER_RETURN'{outs})
            THEN -e.quantity ELSE 0 END),0)::bigint on_hand,
        COALESCE(SUM(CASE WHEN e.action='RESERVE' THEN e.quantity
          WHEN e.action IN ('RELEASE','ISSUE') THEN -e.quantity ELSE 0 END),0)::bigint reserved
      FROM auto_stock_sku sku LEFT JOIN auto_stock_event e ON e.sku_id=sku.id
      GROUP BY sku.id,sku.organization_id,sku.location_id""")


def upgrade():
    op.execute("ALTER TABLE auto_stock_event DROP CONSTRAINT auto_stock_event_action_check")
    op.execute("""ALTER TABLE auto_stock_event ADD CONSTRAINT auto_stock_event_action_check
      CHECK(action IN ('RECEIPT','RESERVE','RELEASE','ISSUE','RETURN','COUNT_GAIN',
        'COUNT_LOSS','TRANSFER_IN','TRANSFER_OUT','WRITE_OFF','SUPPLIER_RETURN',
        'RECLASS_IN','RECLASS_OUT'))""")
    _stock_view(True)
    _stock_guard(True)
    op.execute("""CREATE TABLE auto_stock_reclassification_document(
       id uuid PRIMARY KEY,
       request_id uuid NOT NULL UNIQUE,
       organization_id uuid NOT NULL REFERENCES auto_organization(id),
       location_id uuid NOT NULL REFERENCES auto_service_location(id),
       source_lot_id uuid NOT NULL REFERENCES auto_stock_lot(id),
       destination_lot_id uuid NOT NULL REFERENCES auto_stock_lot(id),
       source_sku_id uuid NOT NULL REFERENCES auto_stock_sku(id),
       destination_sku_id uuid NOT NULL REFERENCES auto_stock_sku(id),
       quantity bigint NOT NULL CHECK(quantity BETWEEN 1 AND 1000000000),
       reason text NOT NULL CHECK(length(btrim(reason)) BETWEEN 3 AND 240),
       created_by uuid NOT NULL REFERENCES app_user(id),
       created_at timestamptz NOT NULL DEFAULT now(),
       CHECK(source_lot_id<>destination_lot_id),
       CHECK(source_sku_id<>destination_sku_id)
    )""")
    op.execute("""CREATE TABLE auto_stock_reclassification_acceptance(
       id uuid PRIMARY KEY,
       request_id uuid NOT NULL UNIQUE,
       document_id uuid NOT NULL UNIQUE REFERENCES auto_stock_reclassification_document(id),
       accepted_by uuid NOT NULL REFERENCES app_user(id),
       debit_allocation_id uuid NOT NULL UNIQUE REFERENCES auto_stock_lot_allocation(id),
       credit_allocation_id uuid NOT NULL UNIQUE REFERENCES auto_stock_lot_allocation(id),
       debit_stock_id uuid NOT NULL UNIQUE REFERENCES auto_stock_event(id),
       credit_stock_id uuid NOT NULL UNIQUE REFERENCES auto_stock_event(id),
       created_at timestamptz NOT NULL DEFAULT now()
    )""")
    for table in ("auto_stock_reclassification_document","auto_stock_reclassification_acceptance"):
        op.execute(f"""CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE
          ON {table} FOR EACH ROW EXECUTE FUNCTION immutable_auto_stock_event()""")
    op.execute("""CREATE FUNCTION guard_auto_stock_reclassification_actor()
      RETURNS trigger LANGUAGE plpgsql AS $fn$
      DECLARE actor uuid; creator uuid; source_lot uuid; source_sku uuid;
      BEGIN
        actor:=NULLIF(current_setting('roboport.reclassification_actor',true),'')::uuid;
        IF actor IS NULL THEN RAISE EXCEPTION 'reclassification actor required'; END IF;
        IF TG_TABLE_NAME='auto_stock_reclassification_document' THEN
          IF NOT EXISTS(
            SELECT 1 FROM auto_stock_lot s
            JOIN auto_stock_lot d ON d.id=NEW.destination_lot_id
            JOIN auto_stock_sku ss ON ss.id=s.sku_id
            JOIN auto_stock_sku ds ON ds.id=d.sku_id
            WHERE s.id=NEW.source_lot_id
              AND s.organization_id=NEW.organization_id AND d.organization_id=NEW.organization_id
              AND s.location_id=NEW.location_id AND d.location_id=NEW.location_id
              AND ss.id=NEW.source_sku_id AND ds.id=NEW.destination_sku_id
              AND ss.organization_id=NEW.organization_id AND ds.organization_id=NEW.organization_id
              AND ss.location_id=NEW.location_id AND ds.location_id=NEW.location_id
          ) THEN RAISE EXCEPTION 'reclassification foreign scope'; END IF;
          NEW.created_by:=actor;
        ELSE
          SELECT created_by,source_lot_id,source_sku_id
            INTO creator,source_lot,source_sku
            FROM auto_stock_reclassification_document
            WHERE id=NEW.document_id FOR SHARE;
          IF NOT FOUND OR creator=actor THEN
            RAISE EXCEPTION 'independent reclassification acceptance required'; END IF;
          -- Direct SQL writers may not bypass the service-level serial embargo.
          PERFORM 1 FROM auto_stock_sku WHERE id=source_sku FOR UPDATE;
          IF EXISTS(SELECT 1 FROM auto_stock_serial WHERE lot_id=source_lot) THEN
            RAISE EXCEPTION 'serialized lot reclassification prohibited'; END IF;
          NEW.accepted_by:=actor;
        END IF;
        RETURN NEW;
      END $fn$""")
    for table in ("auto_stock_reclassification_document","auto_stock_reclassification_acceptance"):
        op.execute(f"""CREATE TRIGGER {table}_actor BEFORE INSERT ON {table}
          FOR EACH ROW EXECUTE FUNCTION guard_auto_stock_reclassification_actor()""")
    op.execute("""CREATE FUNCTION check_auto_stock_reclassification_pair()
      RETURNS trigger LANGUAGE plpgsql AS $fn$
      BEGIN
        IF NOT EXISTS (
          SELECT 1 FROM auto_stock_reclassification_document d
          JOIN auto_stock_lot_allocation da ON da.id=NEW.debit_allocation_id
          JOIN auto_stock_lot_allocation ca ON ca.id=NEW.credit_allocation_id
          JOIN auto_stock_event de ON de.id=NEW.debit_stock_id
          JOIN auto_stock_event ce ON ce.id=NEW.credit_stock_id
          WHERE d.id=NEW.document_id AND da.lot_id=d.source_lot_id
          AND ca.lot_id=d.destination_lot_id
          AND da.sku_id=d.source_sku_id AND ca.sku_id=d.destination_sku_id
          AND da.action='DEALLOCATE' AND ca.action='ALLOCATE'
          AND da.quantity=d.quantity AND ca.quantity=d.quantity
          AND de.sku_id=d.source_sku_id AND ce.sku_id=d.destination_sku_id
          AND de.organization_id=d.organization_id AND ce.organization_id=d.organization_id
          AND de.location_id=d.location_id AND ce.location_id=d.location_id
          AND de.action='RECLASS_OUT' AND ce.action='RECLASS_IN'
          AND de.quantity=d.quantity AND ce.quantity=d.quantity
        ) THEN RAISE EXCEPTION 'unbalanced reclassification'; END IF;
        RETURN NULL;
      END $fn$""")
    op.execute("""CREATE CONSTRAINT TRIGGER auto_stock_reclassification_pair
       AFTER INSERT ON auto_stock_reclassification_acceptance
       DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
       EXECUTE FUNCTION check_auto_stock_reclassification_pair()""")
    # A paired acceptance trigger alone cannot prevent orphan RECLASS stock events.
    # The reverse deferred constraint rejects owner-level INSERT of one physical leg.
    op.execute("""CREATE FUNCTION check_auto_stock_reclassification_stock_event()
      RETURNS trigger LANGUAGE plpgsql AS $fn$
      BEGIN
        IF NOT EXISTS(
          SELECT 1 FROM auto_stock_reclassification_acceptance a
          JOIN auto_stock_reclassification_document d ON d.id=a.document_id
          WHERE (NEW.action='RECLASS_OUT' AND a.debit_stock_id=NEW.id
             AND d.source_sku_id=NEW.sku_id)
             OR (NEW.action='RECLASS_IN' AND a.credit_stock_id=NEW.id
             AND d.destination_sku_id=NEW.sku_id)
        ) THEN RAISE EXCEPTION 'orphan reclassification stock event'; END IF;
        RETURN NULL;
      END $fn$""")
    op.execute("""CREATE CONSTRAINT TRIGGER auto_stock_reclassification_event_pair
      AFTER INSERT ON auto_stock_event DEFERRABLE INITIALLY DEFERRED
      FOR EACH ROW WHEN (NEW.action IN ('RECLASS_IN','RECLASS_OUT'))
      EXECUTE FUNCTION check_auto_stock_reclassification_stock_event()""")
    op.execute("""CREATE FUNCTION record_auto_stock_reclassification_document(
      p_source uuid,p_destination uuid,p_request uuid,p_quantity bigint,p_reason text
    ) RETURNS uuid LANGUAGE plpgsql AS $fn$
      DECLARE src record; dst record; prior record; result_id uuid;
      BEGIN
        IF p_source IS NULL OR p_destination IS NULL OR p_request IS NULL
           OR p_source=p_destination OR p_quantity IS NULL OR p_quantity<1
           OR p_quantity>1000000000 OR p_reason IS NULL OR
           length(btrim(p_reason)) NOT BETWEEN 3 AND 240
        THEN RAISE EXCEPTION 'invalid reclassification document'; END IF;
        SELECT l.sku_id,l.organization_id,l.location_id INTO src
        FROM auto_stock_lot l
        JOIN auto_service_location loc ON loc.id=l.location_id
          AND loc.organization_id=l.organization_id
        JOIN auto_organization org ON org.id=l.organization_id
        WHERE l.id=p_source AND loc.disabled_at IS NULL AND org.disabled_at IS NULL;
        IF NOT FOUND THEN RAISE EXCEPTION 'source lot unavailable'; END IF;
        SELECT l.sku_id,l.organization_id,l.location_id INTO dst
        FROM auto_stock_lot l
        JOIN auto_service_location loc ON loc.id=l.location_id
          AND loc.organization_id=l.organization_id
        JOIN auto_organization org ON org.id=l.organization_id
        WHERE l.id=p_destination AND loc.disabled_at IS NULL AND org.disabled_at IS NULL;
        IF NOT FOUND OR (src.organization_id,src.location_id)
            IS DISTINCT FROM (dst.organization_id,dst.location_id)
          OR src.sku_id=dst.sku_id
        THEN RAISE EXCEPTION 'reclassification requires distinct SKUs within branch'; END IF;
        PERFORM 1 FROM auto_stock_sku
          WHERE id IN (src.sku_id,dst.sku_id) ORDER BY id FOR UPDATE;
        SELECT * INTO prior FROM auto_stock_reclassification_document WHERE request_id=p_request;
        IF FOUND THEN
          IF prior.source_lot_id=p_source AND prior.destination_lot_id=p_destination
           AND prior.quantity=p_quantity AND prior.reason=p_reason
           THEN RETURN prior.id; END IF;
          RAISE EXCEPTION 'reclassification document replay mismatch'; END IF;
        result_id:=gen_random_uuid();
        INSERT INTO auto_stock_reclassification_document(
          id,request_id,organization_id,location_id,source_lot_id,destination_lot_id,
          source_sku_id,destination_sku_id,quantity,reason,created_by)
        VALUES(result_id,p_request,src.organization_id,src.location_id,
          p_source,p_destination,src.sku_id,dst.sku_id,p_quantity,p_reason,
          NULLIF(current_setting('roboport.reclassification_actor',true),'')::uuid);
        RETURN result_id;
      END $fn$""")
    op.execute("""CREATE FUNCTION accept_auto_stock_reclassification_document(
      p_document uuid,p_request uuid
    ) RETURNS uuid LANGUAGE plpgsql AS $fn$
      DECLARE d record; prior record; bal record;
        debit_lot uuid; credit_lot uuid; debit_stock uuid; credit_stock uuid; result_id uuid;
      BEGIN
        IF p_document IS NULL OR p_request IS NULL THEN
          RAISE EXCEPTION 'invalid reclassification acceptance'; END IF;
        SELECT doc.* INTO d FROM auto_stock_reclassification_document doc
          JOIN auto_service_location l ON l.id=doc.location_id
            AND l.organization_id=doc.organization_id
          JOIN auto_organization o ON o.id=doc.organization_id
          WHERE doc.id=p_document AND l.disabled_at IS NULL AND o.disabled_at IS NULL;
        IF NOT FOUND THEN RAISE EXCEPTION 'reclassification unavailable'; END IF;
        PERFORM 1 FROM auto_stock_sku
          WHERE id IN (d.source_sku_id,d.destination_sku_id) ORDER BY id FOR UPDATE;
        SELECT * INTO prior FROM auto_stock_reclassification_acceptance
          WHERE request_id=p_request;
        IF FOUND THEN
          IF prior.document_id=p_document THEN RETURN prior.id; END IF;
          RAISE EXCEPTION 'reclassification acceptance request mismatch'; END IF;
        IF EXISTS(SELECT 1 FROM auto_stock_reclassification_acceptance
           WHERE document_id=p_document) THEN RAISE EXCEPTION 'already accepted'; END IF;
        IF d.created_by IS NOT DISTINCT FROM
             NULLIF(current_setting('roboport.reclassification_actor',true),'')::uuid
        THEN RAISE EXCEPTION 'self acceptance forbidden'; END IF;
        -- Registered serials must not silently disappear during SKU changes.
        -- A separate serial-by-serial reclassification lifecycle is required.
        IF EXISTS(SELECT 1 FROM auto_stock_serial WHERE lot_id=d.source_lot_id)
          THEN RAISE EXCEPTION 'serialized lot reclassification prohibited'; END IF;
        SELECT on_hand,reserved INTO bal FROM auto_stock_balance WHERE sku_id=d.source_sku_id;
        IF bal.on_hand-bal.reserved<d.quantity OR
          (SELECT allocated FROM auto_stock_lot_balance WHERE lot_id=d.source_lot_id)<d.quantity
        THEN RAISE EXCEPTION 'insufficient unreserved reclassification source'; END IF;
        debit_lot:=record_auto_stock_lot_allocation(
            d.source_lot_id,gen_random_uuid(),'DEALLOCATE',d.quantity);
        debit_stock:=gen_random_uuid();
        INSERT INTO auto_stock_event
          (id,request_id,sku_id,organization_id,location_id,action,quantity)
          VALUES(debit_stock,gen_random_uuid(),d.source_sku_id,d.organization_id,
              d.location_id,'RECLASS_OUT',d.quantity);
        credit_stock:=gen_random_uuid();
        INSERT INTO auto_stock_event
          (id,request_id,sku_id,organization_id,location_id,action,quantity)
          VALUES(credit_stock,gen_random_uuid(),d.destination_sku_id,d.organization_id,
              d.location_id,'RECLASS_IN',d.quantity);
        credit_lot:=record_auto_stock_lot_allocation(
             d.destination_lot_id,gen_random_uuid(),'ALLOCATE',d.quantity);
        result_id:=gen_random_uuid();
        INSERT INTO auto_stock_reclassification_acceptance(
          id,request_id,document_id,accepted_by,debit_allocation_id,credit_allocation_id,
          debit_stock_id,credit_stock_id)
        VALUES(result_id,p_request,p_document,
          NULLIF(current_setting('roboport.reclassification_actor',true),'')::uuid,
          debit_lot,credit_lot,debit_stock,credit_stock);
        RETURN result_id;
      END $fn$""")
    op.execute("""REVOKE ALL ON FUNCTION record_auto_stock_reclassification_document(
      uuid,uuid,uuid,bigint,text) FROM PUBLIC""")
    op.execute("""REVOKE ALL ON FUNCTION accept_auto_stock_reclassification_document(
      uuid,uuid) FROM PUBLIC""")
    for table in ("auto_stock_reclassification_document","auto_stock_reclassification_acceptance"):
        op.execute(f"REVOKE ALL ON {table} FROM PUBLIC")


def downgrade():
    op.execute("DROP FUNCTION accept_auto_stock_reclassification_document(uuid,uuid)")
    op.execute("DROP FUNCTION record_auto_stock_reclassification_document(uuid,uuid,uuid,bigint,text)")
    op.execute("DROP TRIGGER auto_stock_reclassification_event_pair ON auto_stock_event")
    op.execute("DROP FUNCTION check_auto_stock_reclassification_stock_event()")
    op.execute("DROP TABLE auto_stock_reclassification_acceptance")
    op.execute("DROP TABLE auto_stock_reclassification_document")
    op.execute("DROP FUNCTION check_auto_stock_reclassification_pair()")
    op.execute("DROP FUNCTION guard_auto_stock_reclassification_actor()")
    _stock_guard(False)
    _stock_view(False)
    op.execute("ALTER TABLE auto_stock_event DROP CONSTRAINT auto_stock_event_action_check")
    op.execute("""ALTER TABLE auto_stock_event ADD CONSTRAINT auto_stock_event_action_check
      CHECK(action IN ('RECEIPT','RESERVE','RELEASE','ISSUE','RETURN','COUNT_GAIN',
        'COUNT_LOSS','TRANSFER_IN','TRANSFER_OUT','WRITE_OFF','SUPPLIER_RETURN'))""")
