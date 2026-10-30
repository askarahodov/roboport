"""Independently approved supplier return with physical and source cost attribution.

Revision ID: 0072_auto_supplier_return
Revises: 0071_auto_serial_movement
"""
from alembic import op

revision = "0072_auto_supplier_return"
down_revision = "0071_auto_serial_movement"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE TABLE auto_stock_supplier_return_document (
      id uuid PRIMARY KEY,
      request_id uuid NOT NULL UNIQUE,
      organization_id uuid NOT NULL REFERENCES auto_organization(id),
      location_id uuid NOT NULL REFERENCES auto_service_location(id),
      lot_id uuid NOT NULL REFERENCES auto_stock_lot(id),
      sku_id uuid NOT NULL REFERENCES auto_stock_sku(id),
      source_receipt_id uuid NOT NULL REFERENCES auto_stock_receipt_document(id),
      quantity bigint NOT NULL CHECK (quantity BETWEEN 1 AND 1000000000),
      reason text NOT NULL CHECK (length(btrim(reason)) BETWEEN 3 AND 240),
      supplier_reference text NOT NULL CHECK
        (length(btrim(supplier_reference)) BETWEEN 3 AND 160),
      created_by uuid NOT NULL REFERENCES app_user(id),
      created_at timestamptz NOT NULL DEFAULT now()
    )""")
    op.execute("""CREATE TABLE auto_stock_supplier_return_acceptance (
      id uuid PRIMARY KEY,
      request_id uuid NOT NULL UNIQUE,
      document_id uuid NOT NULL UNIQUE
        REFERENCES auto_stock_supplier_return_document(id),
      disposition_id uuid NOT NULL UNIQUE
        REFERENCES auto_stock_lot_disposition(id),
      source_valuation_id uuid NOT NULL
        REFERENCES auto_stock_receipt_valuation(id),
      purchase_net_attributed_minor bigint NOT NULL CHECK
        (purchase_net_attributed_minor BETWEEN 0 AND 1000000000000000000),
      purchase_vat_attributed_minor bigint NOT NULL CHECK
        (purchase_vat_attributed_minor BETWEEN 0 AND 1000000000000000000),
      accepted_by uuid NOT NULL REFERENCES app_user(id),
      created_at timestamptz NOT NULL DEFAULT now()
    )""")
    op.execute("""CREATE INDEX ix_auto_stock_supplier_return_receipt
      ON auto_stock_supplier_return_document(source_receipt_id)""")
    for table in ("auto_stock_supplier_return_document",
                  "auto_stock_supplier_return_acceptance"):
        op.execute(f"""CREATE TRIGGER {table}_immutable
          BEFORE UPDATE OR DELETE ON {table}
          FOR EACH ROW EXECUTE FUNCTION immutable_auto_stock_event()""")
    op.execute("""CREATE FUNCTION guard_auto_stock_supplier_return()
      RETURNS trigger LANGUAGE plpgsql AS $fn$
      DECLARE actor uuid; source record; previously_returned bigint;
              valuation record; doc record; new_total bigint; old_total bigint;
      BEGIN
        actor:=NULLIF(current_setting('roboport.supplier_return_actor',true),'')::uuid;
        IF actor IS NULL THEN RAISE EXCEPTION 'supplier return actor required'; END IF;
        IF TG_TABLE_NAME='auto_stock_supplier_return_document' THEN
          SELECT r.lot_id,r.sku_id,r.organization_id,r.location_id,r.quantity
            INTO source FROM auto_stock_receipt_document r
            JOIN auto_stock_receipt_acceptance ra ON ra.document_id=r.id
            JOIN auto_stock_receipt_valuation v ON v.document_id=r.id
            JOIN auto_stock_lot l ON l.id=r.lot_id AND l.sku_id=r.sku_id
              AND l.organization_id=r.organization_id
              AND l.location_id=r.location_id
            WHERE r.id=NEW.source_receipt_id;
          IF NOT FOUND OR (NEW.lot_id,NEW.sku_id,
                           NEW.organization_id,NEW.location_id)
              IS DISTINCT FROM
                           (source.lot_id,source.sku_id,
                            source.organization_id,source.location_id)
          THEN RAISE EXCEPTION 'supplier return must cite valued accepted receipt'; END IF;
          NEW.created_by:=actor;
        ELSE
          SELECT d.*,r.quantity AS source_quantity
            INTO doc FROM auto_stock_supplier_return_document d
            JOIN auto_stock_receipt_document r ON r.id=d.source_receipt_id
            WHERE d.id=NEW.document_id FOR SHARE OF d,r;
          IF NOT FOUND OR doc.created_by=actor THEN
            RAISE EXCEPTION 'independent supplier return approval required'; END IF;
          -- Serializes all approvals citing any receipt of the same SKU.
          PERFORM 1 FROM auto_stock_sku WHERE id=doc.sku_id FOR UPDATE;
          SELECT v.* INTO valuation FROM auto_stock_receipt_valuation v
            WHERE v.document_id=doc.source_receipt_id;
          IF NOT FOUND OR NEW.source_valuation_id<>valuation.id THEN
            RAISE EXCEPTION 'supplier return source valuation unavailable'; END IF;
          SELECT COALESCE(SUM(d.quantity),0)::bigint INTO previously_returned
            FROM auto_stock_supplier_return_acceptance a
            JOIN auto_stock_supplier_return_document d ON d.id=a.document_id
            WHERE d.source_receipt_id=doc.source_receipt_id;
          IF previously_returned+doc.quantity>doc.source_quantity THEN
            RAISE EXCEPTION 'supplier returns exceed valued receipt quantity'; END IF;
          new_total:=valuation.purchase_unit_net_minor*doc.quantity;
          old_total:=floor(valuation.purchase_vat_total_minor::numeric*
                    previously_returned/doc.source_quantity)::bigint;
          NEW.purchase_net_attributed_minor:=new_total;
          NEW.purchase_vat_attributed_minor:=
            floor(valuation.purchase_vat_total_minor::numeric*
              (previously_returned+doc.quantity)/doc.source_quantity)::bigint-old_total;
          NEW.accepted_by:=actor;
        END IF;
        RETURN NEW;
      END $fn$""")
    for table in ("auto_stock_supplier_return_document",
                  "auto_stock_supplier_return_acceptance"):
        op.execute(f"""CREATE TRIGGER {table}_guard BEFORE INSERT ON {table}
          FOR EACH ROW EXECUTE FUNCTION guard_auto_stock_supplier_return()""")
    op.execute("""CREATE FUNCTION check_auto_stock_supplier_return_pair()
      RETURNS trigger LANGUAGE plpgsql AS $fn$
      BEGIN
        IF NOT EXISTS(
          SELECT 1 FROM auto_stock_supplier_return_document d
          JOIN auto_stock_lot_disposition p ON p.id=NEW.disposition_id
          JOIN auto_stock_receipt_valuation v ON v.id=NEW.source_valuation_id
          WHERE d.id=NEW.document_id AND p.lot_id=d.lot_id
            AND p.sku_id=d.sku_id AND p.action='SUPPLIER_RETURN'
            AND p.quantity=d.quantity AND p.reason=d.reason
            AND p.supplier_reference=d.supplier_reference
            AND v.document_id=d.source_receipt_id
        ) THEN RAISE EXCEPTION 'supplier return physical/financial pairing missing';
        END IF;
        RETURN NULL;
      END $fn$""")
    op.execute("""CREATE CONSTRAINT TRIGGER auto_stock_supplier_return_pair
      AFTER INSERT ON auto_stock_supplier_return_acceptance
      DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
      EXECUTE FUNCTION check_auto_stock_supplier_return_pair()""")
    op.execute("""CREATE FUNCTION record_auto_stock_supplier_return_document(
      p_receipt uuid,p_request uuid,p_quantity bigint,
      p_reason text,p_supplier_reference text
    ) RETURNS uuid LANGUAGE plpgsql AS $fn$
      DECLARE source record; old record; result_id uuid;
      BEGIN
        IF p_receipt IS NULL OR p_request IS NULL OR p_quantity IS NULL
          OR p_quantity NOT BETWEEN 1 AND 1000000000
          OR p_reason IS NULL OR length(btrim(p_reason)) NOT BETWEEN 3 AND 240
          OR p_reason<>btrim(p_reason)
          OR p_supplier_reference IS NULL
          OR length(btrim(p_supplier_reference)) NOT BETWEEN 3 AND 160
          OR p_supplier_reference<>btrim(p_supplier_reference)
        THEN RAISE EXCEPTION 'invalid supplier return request'; END IF;
        SELECT r.* INTO source FROM auto_stock_receipt_document r
          JOIN auto_stock_receipt_acceptance a ON a.document_id=r.id
          JOIN auto_stock_receipt_valuation v ON v.document_id=r.id
          JOIN auto_service_location l ON l.id=r.location_id
            AND l.organization_id=r.organization_id
          JOIN auto_organization o ON o.id=r.organization_id
          WHERE r.id=p_receipt AND l.disabled_at IS NULL AND o.disabled_at IS NULL;
        IF NOT FOUND OR source.quantity<p_quantity THEN
          RAISE EXCEPTION 'valued accepted receipt unavailable'; END IF;
        PERFORM 1 FROM auto_stock_sku WHERE id=source.sku_id FOR UPDATE;
        SELECT * INTO old FROM auto_stock_supplier_return_document
          WHERE request_id=p_request;
        IF FOUND THEN
          IF old.source_receipt_id=p_receipt AND old.quantity=p_quantity
           AND old.reason=p_reason AND old.supplier_reference=p_supplier_reference
           AND old.created_by=
             NULLIF(current_setting('roboport.supplier_return_actor',true),'')::uuid
          THEN RETURN old.id; END IF;
          RAISE EXCEPTION 'supplier return replay mismatch'; END IF;
        result_id:=gen_random_uuid();
        INSERT INTO auto_stock_supplier_return_document(
          id,request_id,organization_id,location_id,lot_id,sku_id,
          source_receipt_id,quantity,reason,supplier_reference,created_by)
        VALUES(result_id,p_request,source.organization_id,source.location_id,
          source.lot_id,source.sku_id,p_receipt,p_quantity,p_reason,p_supplier_reference,
          NULLIF(current_setting('roboport.supplier_return_actor',true),'')::uuid);
        RETURN result_id;
      END $fn$""")
    op.execute("""CREATE FUNCTION accept_auto_stock_supplier_return_document(
      p_document uuid,p_request uuid
    ) RETURNS uuid LANGUAGE plpgsql AS $fn$
      DECLARE doc record; old record; value_id uuid;
              disposition_id uuid; result_id uuid; actor uuid;
      BEGIN
        IF p_document IS NULL OR p_request IS NULL THEN
          RAISE EXCEPTION 'invalid supplier return acceptance'; END IF;
        actor:=NULLIF(current_setting('roboport.supplier_return_actor',true),'')::uuid;
        SELECT d.* INTO doc FROM auto_stock_supplier_return_document d
          JOIN auto_service_location l ON l.id=d.location_id
            AND l.organization_id=d.organization_id
          JOIN auto_organization o ON o.id=d.organization_id
          WHERE d.id=p_document AND l.disabled_at IS NULL AND o.disabled_at IS NULL;
        IF NOT FOUND OR actor IS NULL OR actor=doc.created_by THEN
          RAISE EXCEPTION 'supplier return cannot be approved'; END IF;
        PERFORM 1 FROM auto_stock_sku WHERE id=doc.sku_id FOR UPDATE;
        SELECT * INTO old FROM auto_stock_supplier_return_acceptance
          WHERE request_id=p_request;
        IF FOUND THEN
          IF old.document_id=p_document AND old.accepted_by=actor
          THEN RETURN old.id; END IF;
          RAISE EXCEPTION 'supplier return acceptance replay mismatch'; END IF;
        IF EXISTS(SELECT 1 FROM auto_stock_supplier_return_acceptance
                  WHERE document_id=p_document)
        THEN RAISE EXCEPTION 'supplier return already accepted'; END IF;
        SELECT v.id INTO value_id FROM auto_stock_receipt_valuation v
          WHERE v.document_id=doc.source_receipt_id;
        IF NOT FOUND THEN RAISE EXCEPTION 'supplier return valuation missing'; END IF;
        disposition_id:=record_auto_stock_lot_disposition(
          doc.lot_id,gen_random_uuid(),'SUPPLIER_RETURN',
          doc.quantity,doc.reason,doc.supplier_reference);
        result_id:=gen_random_uuid();
        INSERT INTO auto_stock_supplier_return_acceptance(
          id,request_id,document_id,disposition_id,source_valuation_id,
          purchase_net_attributed_minor,purchase_vat_attributed_minor,accepted_by)
        VALUES(result_id,p_request,p_document,disposition_id,value_id,0,0,actor);
        RETURN result_id;
      END $fn$""")
    op.execute("""CREATE VIEW auto_stock_supplier_return_report AS
      SELECT d.id AS document_id,a.id AS acceptance_id,
        d.organization_id,d.location_id,d.lot_id,d.sku_id,d.source_receipt_id,
        d.quantity,d.reason,d.supplier_reference,
        v.currency,a.purchase_net_attributed_minor,
        a.purchase_vat_attributed_minor,
        d.created_by,a.accepted_by,d.created_at AS document_created_at,
        a.created_at AS accepted_at
      FROM auto_stock_supplier_return_document d
      LEFT JOIN auto_stock_supplier_return_acceptance a ON a.document_id=d.id
      JOIN auto_stock_receipt_valuation v ON v.document_id=d.source_receipt_id""")
    for table in ("auto_stock_supplier_return_document",
                  "auto_stock_supplier_return_acceptance"):
        op.execute(f"REVOKE ALL ON {table} FROM PUBLIC")
    op.execute("REVOKE ALL ON auto_stock_supplier_return_report FROM PUBLIC")
    op.execute("""REVOKE ALL ON FUNCTION
      record_auto_stock_supplier_return_document(uuid,uuid,bigint,text,text)
      FROM PUBLIC""")
    op.execute("""REVOKE ALL ON FUNCTION
      accept_auto_stock_supplier_return_document(uuid,uuid) FROM PUBLIC""")


def downgrade():
    op.execute("DROP VIEW auto_stock_supplier_return_report")
    op.execute("DROP FUNCTION accept_auto_stock_supplier_return_document(uuid,uuid)")
    op.execute("DROP FUNCTION record_auto_stock_supplier_return_document(uuid,uuid,bigint,text,text)")
    op.execute("DROP TABLE auto_stock_supplier_return_acceptance")
    op.execute("DROP TABLE auto_stock_supplier_return_document")
    op.execute("DROP FUNCTION check_auto_stock_supplier_return_pair()")
    op.execute("DROP FUNCTION guard_auto_stock_supplier_return()")
