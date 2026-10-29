"""Staged warehouse receipt document and atomic physical acceptance.

Revision ID: 0064_auto_receipt_document
Revises: 0063_auto_stock_disposition
"""
from alembic import op

revision="0064_auto_receipt_document"
down_revision="0063_auto_stock_disposition"
branch_labels=None
depends_on=None


def upgrade():
    op.execute("""CREATE TABLE auto_stock_receipt_document(
      id uuid PRIMARY KEY,
      request_id uuid NOT NULL UNIQUE,
      lot_id uuid NOT NULL REFERENCES auto_stock_lot(id),
      sku_id uuid NOT NULL REFERENCES auto_stock_sku(id),
      organization_id uuid NOT NULL REFERENCES auto_organization(id),
      location_id uuid NOT NULL REFERENCES auto_service_location(id),
      source_reference text NOT NULL CHECK(length(btrim(source_reference)) BETWEEN 3 AND 160),
      quantity bigint NOT NULL CHECK(quantity BETWEEN 1 AND 1000000000),
      created_by uuid NOT NULL REFERENCES app_user(id),
      created_at timestamptz NOT NULL DEFAULT now()
    )""")
    op.execute("""CREATE TABLE auto_stock_receipt_acceptance(
      id uuid PRIMARY KEY,
      request_id uuid NOT NULL UNIQUE,
      document_id uuid NOT NULL UNIQUE REFERENCES auto_stock_receipt_document(id),
      accepted_by uuid NOT NULL REFERENCES app_user(id),
      stock_event_id uuid NOT NULL UNIQUE REFERENCES auto_stock_event(id),
      allocation_event_id uuid NOT NULL UNIQUE REFERENCES auto_stock_lot_allocation(id),
      created_at timestamptz NOT NULL DEFAULT now()
    )""")
    op.execute("""CREATE TRIGGER auto_stock_receipt_document_immutable
      BEFORE UPDATE OR DELETE ON auto_stock_receipt_document
      FOR EACH ROW EXECUTE FUNCTION immutable_auto_stock_event()""")
    op.execute("""CREATE TRIGGER auto_stock_receipt_acceptance_immutable
      BEFORE UPDATE OR DELETE ON auto_stock_receipt_acceptance
      FOR EACH ROW EXECUTE FUNCTION immutable_auto_stock_event()""")
    op.execute("""CREATE FUNCTION guard_auto_stock_receipt_actor()
      RETURNS trigger LANGUAGE plpgsql AS $fn$
      DECLARE actor_id uuid; maker_id uuid;
      BEGIN
        actor_id:=NULLIF(current_setting('roboport.receipt_actor',true),'')::uuid;
        IF actor_id IS NULL THEN RAISE EXCEPTION 'receipt actor required'; END IF;
        IF TG_TABLE_NAME='auto_stock_receipt_document' THEN
          PERFORM 1 FROM auto_stock_lot l
            JOIN auto_stock_sku s ON s.id=l.sku_id
            WHERE l.id=NEW.lot_id AND s.id=NEW.sku_id
              AND l.organization_id=NEW.organization_id
              AND l.location_id=NEW.location_id
              AND s.organization_id=NEW.organization_id
              AND s.location_id=NEW.location_id;
          IF NOT FOUND THEN RAISE EXCEPTION 'foreign receipt scope'; END IF;
          NEW.created_by:=actor_id;
        ELSE
          SELECT created_by INTO maker_id FROM auto_stock_receipt_document
             WHERE id=NEW.document_id FOR SHARE;
          IF NOT FOUND OR actor_id=maker_id THEN
             RAISE EXCEPTION 'independent receipt acceptance required';
          END IF;
          NEW.accepted_by:=actor_id;
        END IF;
        RETURN NEW;
      END $fn$""")
    op.execute("""CREATE TRIGGER auto_stock_receipt_document_actor
      BEFORE INSERT ON auto_stock_receipt_document
      FOR EACH ROW EXECUTE FUNCTION guard_auto_stock_receipt_actor()""")
    op.execute("""CREATE TRIGGER auto_stock_receipt_acceptance_actor
      BEFORE INSERT ON auto_stock_receipt_acceptance
      FOR EACH ROW EXECUTE FUNCTION guard_auto_stock_receipt_actor()""")
    op.execute("""CREATE FUNCTION check_auto_stock_receipt_acceptance()
      RETURNS trigger LANGUAGE plpgsql AS $fn$
      BEGIN
        IF NOT EXISTS(
          SELECT 1 FROM auto_stock_receipt_document d
          JOIN auto_stock_event e ON e.id=NEW.stock_event_id
          JOIN auto_stock_lot_allocation a ON a.id=NEW.allocation_event_id
          WHERE d.id=NEW.document_id AND e.sku_id=d.sku_id
            AND e.organization_id=d.organization_id AND e.location_id=d.location_id
            AND e.action='RECEIPT' AND e.quantity=d.quantity
            AND a.lot_id=d.lot_id AND a.sku_id=d.sku_id
            AND a.action='ALLOCATE' AND a.quantity=d.quantity
        ) THEN RAISE EXCEPTION 'unpaired warehouse receipt acceptance'; END IF;
        RETURN NULL;
      END $fn$""")
    op.execute("""CREATE CONSTRAINT TRIGGER auto_stock_receipt_acceptance_pair
      AFTER INSERT ON auto_stock_receipt_acceptance
      DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
      EXECUTE FUNCTION check_auto_stock_receipt_acceptance()""")
    op.execute("""CREATE FUNCTION record_auto_stock_receipt_document(
      p_lot uuid,p_request uuid,p_quantity bigint,p_source text
    ) RETURNS uuid LANGUAGE plpgsql AS $fn$
      DECLARE lot_row record; prior record; receipt_id uuid;
      BEGIN
        IF p_lot IS NULL OR p_request IS NULL OR p_quantity IS NULL
          OR p_quantity<1 OR p_quantity>1000000000
          OR p_source IS NULL OR length(btrim(p_source)) NOT BETWEEN 3 AND 160
        THEN RAISE EXCEPTION 'invalid receipt document'; END IF;
        SELECT l.sku_id,l.organization_id,l.location_id INTO lot_row
          FROM auto_stock_lot l
          JOIN auto_service_location loc ON loc.id=l.location_id
             AND loc.organization_id=l.organization_id
          JOIN auto_organization org ON org.id=l.organization_id
          WHERE l.id=p_lot AND loc.disabled_at IS NULL AND org.disabled_at IS NULL;
        IF NOT FOUND THEN RAISE EXCEPTION 'receipt lot unavailable'; END IF;
        PERFORM 1 FROM auto_stock_sku WHERE id=lot_row.sku_id FOR UPDATE;
        SELECT * INTO prior FROM auto_stock_receipt_document WHERE request_id=p_request;
        IF FOUND THEN
          IF prior.lot_id=p_lot AND prior.quantity=p_quantity
             AND prior.source_reference=p_source THEN RETURN prior.id; END IF;
          RAISE EXCEPTION 'receipt request mismatch'; END IF;
        receipt_id:=gen_random_uuid();
        INSERT INTO auto_stock_receipt_document(
           id,request_id,lot_id,sku_id,organization_id,location_id,
           source_reference,quantity,created_by)
          VALUES(receipt_id,p_request,p_lot,lot_row.sku_id,
            lot_row.organization_id,lot_row.location_id,p_source,p_quantity,
            NULLIF(current_setting('roboport.receipt_actor',true),'')::uuid);
        RETURN receipt_id;
      END $fn$""")
    op.execute("""CREATE FUNCTION accept_auto_stock_receipt_document(
      p_document uuid,p_request uuid
    ) RETURNS uuid LANGUAGE plpgsql AS $fn$
      DECLARE doc record; existing record; stock_id uuid; allocation_id uuid; acceptance_id uuid;
      BEGIN
        IF p_document IS NULL OR p_request IS NULL THEN
          RAISE EXCEPTION 'invalid acceptance'; END IF;
        SELECT d.* INTO doc FROM auto_stock_receipt_document d
          JOIN auto_service_location loc ON loc.id=d.location_id
            AND loc.organization_id=d.organization_id
          JOIN auto_organization org ON org.id=d.organization_id
          WHERE d.id=p_document AND loc.disabled_at IS NULL AND org.disabled_at IS NULL;
        IF NOT FOUND THEN RAISE EXCEPTION 'receipt unavailable'; END IF;
        PERFORM 1 FROM auto_stock_sku WHERE id=doc.sku_id FOR UPDATE;
        SELECT * INTO existing FROM auto_stock_receipt_acceptance WHERE request_id=p_request;
        IF FOUND THEN
          IF existing.document_id=p_document THEN RETURN existing.id; END IF;
          RAISE EXCEPTION 'acceptance request mismatch'; END IF;
        IF EXISTS(SELECT 1 FROM auto_stock_receipt_acceptance WHERE document_id=p_document)
          THEN RAISE EXCEPTION 'receipt already accepted'; END IF;
        IF NULLIF(current_setting('roboport.receipt_actor',true),'')::uuid
             IS NOT DISTINCT FROM doc.created_by THEN
          RAISE EXCEPTION 'self acceptance forbidden'; END IF;
        stock_id:=gen_random_uuid();
        INSERT INTO auto_stock_event(
          id,request_id,sku_id,organization_id,location_id,action,quantity)
          VALUES(stock_id,gen_random_uuid(),doc.sku_id,doc.organization_id,
            doc.location_id,'RECEIPT',doc.quantity);
        allocation_id:=record_auto_stock_lot_allocation(
          doc.lot_id,gen_random_uuid(),'ALLOCATE',doc.quantity);
        acceptance_id:=gen_random_uuid();
        INSERT INTO auto_stock_receipt_acceptance(
          id,request_id,document_id,accepted_by,stock_event_id,allocation_event_id)
          VALUES(acceptance_id,p_request,p_document,
           NULLIF(current_setting('roboport.receipt_actor',true),'')::uuid,
           stock_id,allocation_id);
        RETURN acceptance_id;
      END $fn$""")
    op.execute("REVOKE ALL ON FUNCTION record_auto_stock_receipt_document(uuid,uuid,bigint,text) FROM PUBLIC")
    op.execute("REVOKE ALL ON FUNCTION accept_auto_stock_receipt_document(uuid,uuid) FROM PUBLIC")


def downgrade():
    op.execute("DROP FUNCTION accept_auto_stock_receipt_document(uuid,uuid)")
    op.execute("DROP FUNCTION record_auto_stock_receipt_document(uuid,uuid,bigint,text)")
    op.execute("DROP TABLE auto_stock_receipt_acceptance")
    op.execute("DROP TABLE auto_stock_receipt_document")
    op.execute("DROP FUNCTION check_auto_stock_receipt_acceptance()")
    op.execute("DROP FUNCTION guard_auto_stock_receipt_actor()")
