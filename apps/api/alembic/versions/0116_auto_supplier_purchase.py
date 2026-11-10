"""Synthetic procurement: scoped immutable order, partial receipts and remainder cancellation.

Revision ID: 0116_auto_supplier_purchase
Revises: 0115_demo_preview_exact_keys
No real suppliers, stock posting, payments or external purchasing authority.
"""
from alembic import op

revision = "0116_auto_supplier_purchase"
down_revision = "0115_demo_preview_exact_keys"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
      CREATE TABLE auto_supplier_purchase_order (
        id uuid PRIMARY KEY,
        request_id uuid NOT NULL UNIQUE,
        organization_id uuid NOT NULL REFERENCES auto_organization(id),
        location_id uuid NOT NULL REFERENCES auto_service_location(id),
        work_order_id uuid NOT NULL REFERENCES auto_work_order(id),
        sku_id uuid NOT NULL REFERENCES auto_stock_sku(id),
        lot_id uuid NOT NULL REFERENCES auto_stock_lot(id),
        desired_quantity bigint NOT NULL CHECK (desired_quantity BETWEEN 1 AND 1000000000),
        available_at_order bigint NOT NULL CHECK (available_at_order >= 0),
        ordered_quantity bigint NOT NULL CHECK (ordered_quantity BETWEEN 1 AND 1000000000),
        offers jsonb NOT NULL CHECK (jsonb_typeof(offers)='array' AND jsonb_array_length(offers) BETWEEN 2 AND 5),
        selected_supplier text NOT NULL CHECK (length(btrim(selected_supplier)) BETWEEN 1 AND 160),
        selected_offer text NOT NULL CHECK (length(btrim(selected_offer)) BETWEEN 1 AND 160),
        quote_revision text NOT NULL CHECK (length(btrim(quote_revision)) BETWEEN 1 AND 160),
        quoted_at timestamptz NOT NULL,
        expires_at timestamptz NOT NULL,
        currency text NOT NULL CHECK (currency='RUB'),
        purchase_unit_minor bigint NOT NULL CHECK (purchase_unit_minor BETWEEN 0 AND 1000000000),
        created_by uuid NOT NULL REFERENCES app_user(id),
        created_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE(work_order_id,sku_id)
      )
    """)
    op.execute("""
      CREATE TABLE auto_supplier_purchase_receipt (
        id uuid PRIMARY KEY,
        request_id uuid NOT NULL UNIQUE,
        purchase_id uuid NOT NULL REFERENCES auto_supplier_purchase_order(id),
        document_id uuid NOT NULL UNIQUE REFERENCES auto_stock_receipt_document(id),
        quantity bigint NOT NULL CHECK (quantity BETWEEN 1 AND 1000000000),
        created_by uuid NOT NULL REFERENCES app_user(id),
        created_at timestamptz NOT NULL DEFAULT now()
      )
    """)
    op.execute("""
      CREATE INDEX ix_auto_supplier_purchase_receipt_purchase
      ON auto_supplier_purchase_receipt(purchase_id)
    """)
    op.execute("""
      CREATE TABLE auto_supplier_purchase_cancel (
        id uuid PRIMARY KEY,
        request_id uuid NOT NULL UNIQUE,
        purchase_id uuid NOT NULL UNIQUE REFERENCES auto_supplier_purchase_order(id),
        canceled_remainder bigint NOT NULL CHECK (canceled_remainder BETWEEN 1 AND 1000000000),
        created_by uuid NOT NULL REFERENCES app_user(id),
        created_at timestamptz NOT NULL DEFAULT now()
      )
    """)
    for table in ("auto_supplier_purchase_order", "auto_supplier_purchase_receipt",
                  "auto_supplier_purchase_cancel"):
        op.execute(f"""CREATE TRIGGER {table}_immutable
          BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW
          EXECUTE FUNCTION immutable_auto_stock_event()""")
    op.execute("""
      CREATE FUNCTION guard_auto_supplier_purchase_order()
      RETURNS trigger LANGUAGE plpgsql AS $fn$
      DECLARE actor uuid; work record; stock record; quote jsonb; matching_count int;
      BEGIN
        actor:=NULLIF(current_setting('roboport.purchase_actor',true),'')::uuid;
        IF actor IS NULL OR NEW.created_by<>actor
           OR NEW.expires_at<=now() OR NEW.expires_at<=NEW.quoted_at
           OR NEW.purchase_unit_minor<0 THEN
          RAISE EXCEPTION 'invalid supplier purchase identity or quote';
        END IF;
        SELECT w.organization_id,w.location_id INTO work
          FROM auto_work_order w
          JOIN auto_appointment a ON a.id=w.appointment_id
            AND a.organization_id=w.organization_id AND a.location_id=w.location_id
          JOIN auto_bay_booking b ON b.id=a.booking_id
          JOIN auto_organization o ON o.id=w.organization_id
          JOIN auto_service_location l ON l.id=w.location_id
            AND l.organization_id=w.organization_id
          WHERE w.id=NEW.work_order_id AND b.canceled_at IS NULL
            AND o.disabled_at IS NULL AND l.disabled_at IS NULL
          FOR SHARE OF w,a,b,o,l;
        IF NOT FOUND OR (work.organization_id,work.location_id)
          IS DISTINCT FROM (NEW.organization_id,NEW.location_id) THEN
          RAISE EXCEPTION 'supplier order work order scope unavailable';
        END IF;
        SELECT s.id INTO stock FROM auto_stock_sku s
          JOIN auto_stock_lot lot ON lot.sku_id=s.id
            AND lot.organization_id=s.organization_id
            AND lot.location_id=s.location_id
          WHERE s.id=NEW.sku_id AND lot.id=NEW.lot_id
            AND s.organization_id=NEW.organization_id
            AND s.location_id=NEW.location_id FOR SHARE OF lot;
        IF NOT FOUND THEN RAISE EXCEPTION 'supplier purchase lot scope mismatch'; END IF;
        SELECT greatest(on_hand-reserved,0)::bigint INTO stock
          FROM auto_stock_balance WHERE sku_id=NEW.sku_id;
        IF NEW.available_at_order<>stock.id OR
           NEW.ordered_quantity<>NEW.desired_quantity-stock.id OR
           NEW.ordered_quantity<1 THEN
          RAISE EXCEPTION 'supplier order does not match current shortage';
        END IF;
        SELECT count(*) INTO matching_count
          FROM jsonb_array_elements(NEW.offers) AS q(value)
          WHERE q.value->>'supplier_id'=NEW.selected_supplier
            AND q.value->>'offer_id'=NEW.selected_offer
            AND q.value->>'source_revision'=NEW.quote_revision
            AND q.value->>'currency'=NEW.currency
            AND (q.value->>'unit_purchase_minor')::bigint=NEW.purchase_unit_minor
            AND (q.value->>'available_quantity')::bigint>=NEW.ordered_quantity
            AND (q.value->>'quoted_at')::timestamptz=NEW.quoted_at
            AND (q.value->>'expires_at')::timestamptz=NEW.expires_at;
        IF matching_count<>1 THEN RAISE EXCEPTION 'supplier quote mismatch'; END IF;
        RETURN NEW;
      END $fn$
    """)
    op.execute("""
      CREATE TRIGGER auto_supplier_purchase_order_guard
      BEFORE INSERT ON auto_supplier_purchase_order
      FOR EACH ROW EXECUTE FUNCTION guard_auto_supplier_purchase_order()
    """)
    op.execute("""
      CREATE FUNCTION guard_auto_supplier_purchase_event()
      RETURNS trigger LANGUAGE plpgsql AS $fn$
      DECLARE actor uuid; purchase record; delivered bigint; doc record;
      BEGIN
        actor:=NULLIF(current_setting('roboport.purchase_actor',true),'')::uuid;
        IF actor IS NULL OR NEW.created_by<>actor THEN
          RAISE EXCEPTION 'supplier purchase actor required';
        END IF;
        SELECT * INTO purchase FROM auto_supplier_purchase_order
          WHERE id=NEW.purchase_id FOR UPDATE;
        IF NOT FOUND THEN RAISE EXCEPTION 'supplier order unavailable'; END IF;
        SELECT COALESCE(SUM(quantity),0)::bigint INTO delivered
          FROM auto_supplier_purchase_receipt WHERE purchase_id=NEW.purchase_id;
        IF TG_TABLE_NAME='auto_supplier_purchase_receipt' THEN
          IF EXISTS(SELECT 1 FROM auto_supplier_purchase_cancel
                    WHERE purchase_id=NEW.purchase_id)
             OR delivered+NEW.quantity>purchase.ordered_quantity THEN
             RAISE EXCEPTION 'overreceipt or canceled supplier order';
          END IF;
          SELECT lot_id,sku_id,organization_id,location_id,quantity,
                 created_by INTO doc
            FROM auto_stock_receipt_document WHERE id=NEW.document_id FOR SHARE;
          IF NOT FOUND OR
            (doc.lot_id,doc.sku_id,doc.organization_id,doc.location_id,
             doc.quantity,doc.created_by) IS DISTINCT FROM
            (purchase.lot_id,purchase.sku_id,purchase.organization_id,
             purchase.location_id,NEW.quantity,actor)
          THEN RAISE EXCEPTION 'supplier receipt mismatched stock document'; END IF;
        ELSE
          IF delivered>=purchase.ordered_quantity
             OR NEW.canceled_remainder<>purchase.ordered_quantity-delivered THEN
             RAISE EXCEPTION 'nothing left to cancel';
          END IF;
        END IF;
        RETURN NEW;
      END $fn$
    """)
    for table in ("auto_supplier_purchase_receipt", "auto_supplier_purchase_cancel"):
        op.execute(f"""CREATE TRIGGER {table}_guard BEFORE INSERT ON {table}
          FOR EACH ROW EXECUTE FUNCTION guard_auto_supplier_purchase_event()""")
    op.execute("""
      REVOKE ALL ON TABLE auto_supplier_purchase_order,
        auto_supplier_purchase_receipt, auto_supplier_purchase_cancel FROM PUBLIC
    """)


def downgrade():
    # Disposable dev database only; deliberate reverse order for linked immutable records.
    for table in ("auto_supplier_purchase_cancel", "auto_supplier_purchase_receipt",
                  "auto_supplier_purchase_order"):
        op.execute(f"DROP TABLE {table}")
    op.execute("DROP FUNCTION guard_auto_supplier_purchase_event()")
    op.execute("DROP FUNCTION guard_auto_supplier_purchase_order()")
