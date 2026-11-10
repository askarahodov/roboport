"""Procurement order guard follows rescheduled appointment's latest active booking.

Revision ID: 0123_supplier_booking
Revises: 0122_auto_financial_document_acl

No destructive schema change and no real supplier/purchasing enablement.
The append-only purchase audit/quote/stock invariants remain intact.
"""
from alembic import op

revision="0123_supplier_booking"
down_revision="0122_auto_financial_document_acl"
branch_labels=None
depends_on=None


def upgrade():
    # Exact existing guard from 0116, except resolution of the latest
    # immutable reschedule event. The old slot is canceled by a move.
    op.execute("""      CREATE OR REPLACE FUNCTION guard_auto_supplier_purchase_order()
      RETURNS trigger LANGUAGE plpgsql AS $fn$
      DECLARE actor uuid; work record; sku_ok uuid; available_qty bigint; matching_count int;
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
          JOIN LATERAL (
            SELECT COALESCE((
              SELECT r.new_booking_id FROM auto_appointment_reschedule r
              WHERE r.appointment_id=a.id
              ORDER BY r.created_at DESC,r.id DESC LIMIT 1
            ),a.booking_id) AS current_booking_id
          ) latest ON TRUE
          JOIN auto_bay_booking b ON b.id=latest.current_booking_id
            AND b.organization_id=w.organization_id
            AND b.location_id=w.location_id
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
        SELECT s.id INTO sku_ok FROM auto_stock_sku s
          JOIN auto_stock_lot lot ON lot.sku_id=s.id
            AND lot.organization_id=s.organization_id
            AND lot.location_id=s.location_id
          WHERE s.id=NEW.sku_id AND lot.id=NEW.lot_id
            AND s.organization_id=NEW.organization_id
            AND s.location_id=NEW.location_id FOR SHARE OF lot;
        IF NOT FOUND THEN RAISE EXCEPTION 'supplier purchase lot scope mismatch'; END IF;
        SELECT greatest(on_hand-reserved,0)::bigint INTO available_qty
          FROM auto_stock_balance WHERE sku_id=NEW.sku_id;
        IF NEW.available_at_order<>available_qty OR
           NEW.ordered_quantity<>NEW.desired_quantity-available_qty OR
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


def downgrade():
    # Disposable synthetic PG17 only. Existing purchase records are immutable.
    # Downgrade restores the old guard but cannot undo procurement history.
    op.execute("""      CREATE OR REPLACE FUNCTION guard_auto_supplier_purchase_order()
      RETURNS trigger LANGUAGE plpgsql AS $fn$
      DECLARE actor uuid; work record; sku_ok uuid; available_qty bigint; matching_count int;
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
        SELECT s.id INTO sku_ok FROM auto_stock_sku s
          JOIN auto_stock_lot lot ON lot.sku_id=s.id
            AND lot.organization_id=s.organization_id
            AND lot.location_id=s.location_id
          WHERE s.id=NEW.sku_id AND lot.id=NEW.lot_id
            AND s.organization_id=NEW.organization_id
            AND s.location_id=NEW.location_id FOR SHARE OF lot;
        IF NOT FOUND THEN RAISE EXCEPTION 'supplier purchase lot scope mismatch'; END IF;
        SELECT greatest(on_hand-reserved,0)::bigint INTO available_qty
          FROM auto_stock_balance WHERE sku_id=NEW.sku_id;
        IF NEW.available_at_order<>available_qty OR
           NEW.ordered_quantity<>NEW.desired_quantity-available_qty OR
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
