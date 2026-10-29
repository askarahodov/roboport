"""Immutable financial provenance for accepted automotive warehouse receipts.

Revision ID: 0065_auto_receipt_valuation
Revises: 0064_auto_receipt_document
"""
from alembic import op

revision="0065_auto_receipt_valuation"
down_revision="0064_auto_receipt_document"
branch_labels=None
depends_on=None


def upgrade():
    op.execute("""CREATE TABLE auto_stock_receipt_valuation(
      id uuid PRIMARY KEY,
      request_id uuid NOT NULL UNIQUE,
      document_id uuid NOT NULL UNIQUE REFERENCES auto_stock_receipt_document(id),
      currency text NOT NULL CHECK(currency ~ '^[A-Z]{3}$'),
      purchase_unit_net_minor bigint NOT NULL
        CHECK(purchase_unit_net_minor BETWEEN 0 AND 1000000000),
      purchase_vat_total_minor bigint NOT NULL
        CHECK(purchase_vat_total_minor BETWEEN 0 AND 1000000000000000000),
      supplier_price_revision text NOT NULL
        CHECK(length(btrim(supplier_price_revision)) BETWEEN 3 AND 160),
      supplier_quoted_at timestamptz NOT NULL,
      valued_by uuid NOT NULL REFERENCES app_user(id),
      created_at timestamptz NOT NULL DEFAULT now()
    )""")
    op.execute("""CREATE TRIGGER auto_stock_receipt_valuation_immutable
      BEFORE UPDATE OR DELETE ON auto_stock_receipt_valuation
      FOR EACH ROW EXECUTE FUNCTION immutable_auto_stock_event()""")
    op.execute("""CREATE FUNCTION guard_auto_stock_receipt_valuation()
      RETURNS trigger LANGUAGE plpgsql AS $fn$
      DECLARE actor_id uuid; maker_id uuid;
      BEGIN
        actor_id:=NULLIF(current_setting('roboport.receipt_value_actor',true),'')::uuid;
        IF actor_id IS NULL THEN
          RAISE EXCEPTION 'receipt valuation actor required'; END IF;
        SELECT d.created_by INTO maker_id
          FROM auto_stock_receipt_document d
          JOIN auto_stock_receipt_acceptance a ON a.document_id=d.id
          WHERE d.id=NEW.document_id FOR SHARE OF d,a;
        IF NOT FOUND THEN RAISE EXCEPTION 'receipt acceptance required'; END IF;
        IF maker_id=actor_id THEN
          RAISE EXCEPTION 'receipt financial reviewer must be independent'; END IF;
        IF NEW.supplier_quoted_at>now()+interval '5 minutes' THEN
          RAISE EXCEPTION 'future supplier quote timestamp'; END IF;
        NEW.valued_by:=actor_id;
        RETURN NEW;
      END $fn$""")
    op.execute("""CREATE TRIGGER auto_stock_receipt_valuation_actor
      BEFORE INSERT ON auto_stock_receipt_valuation
      FOR EACH ROW EXECUTE FUNCTION guard_auto_stock_receipt_valuation()""")
    op.execute("""CREATE VIEW auto_stock_receipt_valuation_report AS
      SELECT v.id AS valuation_id,v.document_id,
        d.organization_id,d.location_id,d.sku_id,d.lot_id,d.quantity,
        v.currency,v.purchase_unit_net_minor,
        (v.purchase_unit_net_minor*d.quantity)::bigint AS purchase_net_total_minor,
        v.purchase_vat_total_minor,
        (v.purchase_unit_net_minor*d.quantity+v.purchase_vat_total_minor)::bigint
          AS purchase_gross_total_minor,
        v.supplier_price_revision,v.supplier_quoted_at,
        d.source_reference AS receipt_source_reference,
        v.valued_by,v.created_at
      FROM auto_stock_receipt_valuation v
      JOIN auto_stock_receipt_document d ON d.id=v.document_id""")
    op.execute("""CREATE FUNCTION record_auto_stock_receipt_valuation(
      p_document uuid,p_request uuid,p_currency text,
      p_unit_net bigint,p_vat_total bigint,p_revision text,
      p_source_at timestamptz
    ) RETURNS uuid LANGUAGE plpgsql AS $fn$
      DECLARE doc record; prior record; value_id uuid;
      BEGIN
        IF p_document IS NULL OR p_request IS NULL OR p_currency IS NULL
          OR p_currency !~ '^[A-Z]{3}$'
          OR p_unit_net IS NULL OR p_unit_net<0 OR p_unit_net>1000000000
          OR p_vat_total IS NULL OR p_vat_total<0
             OR p_vat_total>1000000000000000000
          OR p_revision IS NULL OR length(btrim(p_revision)) NOT BETWEEN 3 AND 160
          OR p_source_at IS NULL OR p_source_at>now()+interval '5 minutes'
        THEN RAISE EXCEPTION 'invalid receipt valuation'; END IF;
        SELECT d.* INTO doc FROM auto_stock_receipt_document d
          JOIN auto_stock_receipt_acceptance a ON a.document_id=d.id
          JOIN auto_service_location l ON l.id=d.location_id
            AND l.organization_id=d.organization_id
          JOIN auto_organization o ON o.id=d.organization_id
          WHERE d.id=p_document AND l.disabled_at IS NULL AND o.disabled_at IS NULL;
        IF NOT FOUND THEN RAISE EXCEPTION 'receipt not accepted'; END IF;
        PERFORM 1 FROM auto_stock_sku WHERE id=doc.sku_id FOR UPDATE;
        SELECT * INTO prior FROM auto_stock_receipt_valuation WHERE request_id=p_request;
        IF FOUND THEN
          IF prior.document_id=p_document AND prior.currency=p_currency
            AND prior.purchase_unit_net_minor=p_unit_net
            AND prior.purchase_vat_total_minor=p_vat_total
            AND prior.supplier_price_revision=p_revision
            AND prior.supplier_quoted_at=p_source_at
          THEN RETURN prior.id; END IF;
          RAISE EXCEPTION 'valuation request replay mismatch'; END IF;
        IF EXISTS(SELECT 1 FROM auto_stock_receipt_valuation
                  WHERE document_id=p_document) THEN
          RAISE EXCEPTION 'receipt already valued'; END IF;
        value_id:=gen_random_uuid();
        INSERT INTO auto_stock_receipt_valuation(
          id,request_id,document_id,currency,purchase_unit_net_minor,
          purchase_vat_total_minor,supplier_price_revision,supplier_quoted_at,valued_by)
          VALUES(value_id,p_request,p_document,p_currency,p_unit_net,
            p_vat_total,p_revision,p_source_at,
            NULLIF(current_setting('roboport.receipt_value_actor',true),'')::uuid);
        RETURN value_id;
      END $fn$""")
    op.execute("""REVOKE ALL ON FUNCTION record_auto_stock_receipt_valuation(
      uuid,uuid,text,bigint,bigint,text,timestamptz) FROM PUBLIC""")


def downgrade():
    op.execute("""DROP FUNCTION record_auto_stock_receipt_valuation(
      uuid,uuid,text,bigint,bigint,text,timestamptz)""")
    op.execute("DROP VIEW auto_stock_receipt_valuation_report")
    op.execute("DROP TABLE auto_stock_receipt_valuation")
    op.execute("DROP FUNCTION guard_auto_stock_receipt_valuation()")
