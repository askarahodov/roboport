"""Private synthetic work-order shortage → supplier order → partial receipts.

No external supplier connection, purchasing authority or live payment.
Server roles and object scopes are rechecked for every call.
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4
import json
import psycopg
from psycopg.types.json import Jsonb

from .auto_org_authority import organization_role_allows
from .auto_parts_provider import CatalogError
from .auto_supplier_offer import parse_supplier_offers
from .auto_stock_receipt_document import create_receipt_document
from .auto_stock_service import AutoStockDenied, AutoStockConflict
from .session_lookup import session_subject
from .auto_document_access import (
    require_document_access,has_document_access,DocumentPolicyDenied,
)


def _valid_ids(*values: UUID) -> bool:
    return all(type(value) is UUID for value in values)


def _role(conn, token: str, org: UUID, loc: UUID, role: str = "WAREHOUSE",
          cap: str = "CREATE") -> UUID:
    if type(token) is not str or not token or not organization_role_allows(
            conn, token, org, role, loc, for_mutation=True):
        raise AutoStockDenied("FORBIDDEN")
    actor = session_subject(conn, token, for_mutation=True)
    if actor is None:
        raise AutoStockDenied("FORBIDDEN")
    try:
        require_document_access(conn,token,org,loc,("MANAGER","WAREHOUSE"),"SUPPLIER_ORDER",cap)
    except DocumentPolicyDenied:
        raise AutoStockDenied("FORBIDDEN") from None
    return actor


def _source(offers: object, supplier: str, offer_id: str):
    try:
        parsed = parse_supplier_offers(offers)
    except (CatalogError, ValueError, TypeError):
        raise AutoStockDenied("INVALID_INPUT") from None
    if (len(parsed) < 2 or len(parsed) > 5 or
        len({x.supplier_id for x in parsed}) < 2 or
        type(supplier) is not str or type(offer_id) is not str):
        raise AutoStockDenied("INVALID_INPUT")
    chosen = [x for x in parsed if x.supplier_id == supplier and x.offer_id == offer_id]
    if len(chosen) != 1 or chosen[0].currency != "RUB":
        raise AutoStockDenied("INVALID_INPUT")
    # Preserve the provider's original fixture metadata, not UI-formatted currency.
    snapshot = json.dumps(offers, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return chosen[0], snapshot, parsed


def _conflict_errors(operation):
    conn, actor, write = operation
    try:
        # The nested transaction is a PG savepoint. A failed constraint must
        # roll back before the outer connection can execute any more SQL.
        with conn.transaction():
            conn.execute("SELECT set_config('roboport.purchase_actor',%s,true)",
                         (str(actor),))
            result = write()
            conn.execute("SELECT set_config('roboport.purchase_actor','',true)")
            return result
    except (psycopg.errors.RaiseException, psycopg.errors.UniqueViolation,
            psycopg.errors.CheckViolation, psycopg.errors.ForeignKeyViolation,
            psycopg.errors.NumericValueOutOfRange, psycopg.errors.InvalidTextRepresentation):
        raise AutoStockConflict("CONFLICT") from None


def place_supplier_order(conn: psycopg.Connection, token: str,
                         org: UUID, loc: UUID, work_order: UUID, lot: UUID,
                         request_id: UUID, desired_quantity: int,
                         offers: object, supplier: str, offer_id: str) -> tuple[UUID, int]:
    if (not _valid_ids(org, loc, work_order, lot, request_id) or
        type(desired_quantity) is not int or not 1 <= desired_quantity <= 1_000_000_000):
        raise AutoStockDenied("INVALID_INPUT")
    selected, snapshot, parsed = _source(offers, supplier, offer_id)
    with conn.transaction():
        actor = _role(conn, token, org, loc)
        try:
            require_document_access(conn,token,org,loc,("WAREHOUSE","MANAGER"),
                                    "SUPPLIER_ORDER","PRICE_PURCHASE")
        except DocumentPolicyDenied:
            raise AutoStockDenied("FORBIDDEN") from None
        scope = conn.execute("""SELECT s.id,s.article FROM auto_stock_lot l
          JOIN auto_stock_sku s ON s.id=l.sku_id
            AND s.organization_id=l.organization_id AND s.location_id=l.location_id
          WHERE l.id=%s AND l.organization_id=%s AND l.location_id=%s
          FOR UPDATE OF s""", (lot, org, loc)).fetchone()
        if scope is None:
            raise AutoStockDenied("FORBIDDEN")
        sku_id, article = scope
        if any(x.article != article for x in parsed):
            raise AutoStockDenied("INVALID_INPUT")
        old = conn.execute("""SELECT id,work_order_id,lot_id,desired_quantity,
             offers,selected_supplier,selected_offer,created_by,ordered_quantity
          FROM auto_supplier_purchase_order WHERE request_id=%s""",
                           (request_id,)).fetchone()
        if old is not None:
            if (old[1:4] == (work_order, lot, desired_quantity) and
                old[4] == json.loads(snapshot) and old[5:7] == (supplier, offer_id)
                and old[7] == actor):
                return old[0], old[8]
            raise AutoStockConflict("REPLAY_MISMATCH")
        # Exact replay stays possible after offer expiry; no *new* order may
        # be created against a stale quote.
        if selected.expires_at <= datetime.now(timezone.utc):
            raise AutoStockConflict("EXPIRED_QUOTE")
        # Reschedule keeps auto_appointment.booking_id immutable, while
        # canceling that original bay booking. Use only the latest live booking.
        # A non-current or canceled slot never authorizes purchasing.
        work = conn.execute("""SELECT w.id FROM auto_work_order w
          JOIN auto_appointment a ON a.id=w.appointment_id
            AND a.organization_id=w.organization_id
            AND a.location_id=w.location_id
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
          WHERE w.id=%s AND w.organization_id=%s AND w.location_id=%s
            AND b.canceled_at IS NULL AND o.disabled_at IS NULL
            AND l.disabled_at IS NULL
          FOR SHARE OF w,a,b,o,l""",(work_order,org,loc)).fetchone()
        if work is None:
            raise AutoStockDenied("FORBIDDEN")
        balance = conn.execute(
            "SELECT greatest(on_hand-reserved,0)::bigint FROM auto_stock_balance WHERE sku_id=%s",
            (sku_id,)).fetchone()
        available = balance[0] if balance else 0
        shortage = desired_quantity - available
        if shortage <= 0 or selected.available_quantity < shortage:
            raise AutoStockConflict("NO_SHORTAGE_OR_QUOTE")
        def insert():
            ident=uuid4()
            conn.execute("""INSERT INTO auto_supplier_purchase_order(
              id,request_id,organization_id,location_id,work_order_id,sku_id,lot_id,
              desired_quantity,available_at_order,ordered_quantity,offers,
              selected_supplier,selected_offer,quote_revision,quoted_at,expires_at,
              currency,purchase_unit_minor,created_by)
              VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,(ident,request_id,org,loc,work_order,sku_id,lot,
                  desired_quantity,available,shortage,Jsonb(json.loads(snapshot)),
                  selected.supplier_id,selected.offer_id,selected.source_revision,
                  selected.quoted_at,selected.expires_at,selected.currency,
                  selected.unit_purchase_minor,actor))
            return ident,shortage
        return _conflict_errors((conn,actor,insert))


def record_supplier_delivery(conn: psycopg.Connection, token: str, org: UUID,
                             loc: UUID, purchase: UUID, request_id: UUID,
                             quantity: int, supplier_reference: str) -> tuple[UUID, UUID]:
    if (not _valid_ids(org,loc,purchase,request_id) or
        type(quantity) is not int or not 1<=quantity<=1_000_000_000 or
        type(supplier_reference) is not str or
        not 3<=len(supplier_reference.strip())<=160 or
        supplier_reference!=supplier_reference.strip()):
        raise AutoStockDenied("INVALID_INPUT")
    with conn.transaction():
        actor=_role(conn,token,org,loc,cap="EDIT")
        row=conn.execute("""SELECT id,lot_id,ordered_quantity FROM auto_supplier_purchase_order
          WHERE id=%s AND organization_id=%s AND location_id=%s FOR UPDATE""",
          (purchase,org,loc)).fetchone()
        if row is None:
            raise AutoStockDenied("FORBIDDEN")
        prior=conn.execute("""SELECT p.id,p.document_id,p.purchase_id,p.quantity,
                 p.created_by,d.source_reference
            FROM auto_supplier_purchase_receipt p
            JOIN auto_stock_receipt_document d ON d.id=p.document_id
            WHERE p.request_id=%s""",(request_id,)).fetchone()
        if prior:
            if (prior[2:5]==(purchase,quantity,actor)
                and prior[5]==supplier_reference):
                return prior[0],prior[1]
            raise AutoStockConflict("REPLAY_MISMATCH")
        canceled=conn.execute("""SELECT id FROM auto_supplier_purchase_cancel
          WHERE purchase_id=%s""",(purchase,)).fetchone()
        delivered=conn.execute("""SELECT COALESCE(SUM(quantity),0)::bigint
          FROM auto_supplier_purchase_receipt WHERE purchase_id=%s""",
            (purchase,)).fetchone()[0]
        if canceled or delivered+quantity>row[2]:
            raise AutoStockConflict("ORDER_CANCELED_OR_OVERRECEIPT")
        # Existing maker/checker receipt is authoritative; one transaction
        # registers its draft *and* the supplier-order provenance link.
        def insert():
            document_id=create_receipt_document(
            conn,token,org,loc,row[1],request_id,quantity,supplier_reference)
            event_id=uuid4()
            conn.execute("""INSERT INTO auto_supplier_purchase_receipt(
                id,request_id,purchase_id,document_id,quantity,created_by)
              VALUES(%s,%s,%s,%s,%s,%s)""",
              (event_id,request_id,purchase,document_id,quantity,actor))
            return event_id,document_id
        return _conflict_errors((conn,actor,insert))


def cancel_supplier_remainder(conn: psycopg.Connection, token: str, org: UUID,
                              loc: UUID, purchase: UUID, request_id: UUID) -> tuple[UUID,int]:
    if not _valid_ids(org,loc,purchase,request_id):
        raise AutoStockDenied("INVALID_INPUT")
    with conn.transaction():
        actor=_role(conn,token,org,loc,cap="CLOSE")
        row=conn.execute("""SELECT id,ordered_quantity FROM auto_supplier_purchase_order
          WHERE id=%s AND organization_id=%s AND location_id=%s FOR UPDATE""",
          (purchase,org,loc)).fetchone()
        if row is None:
            raise AutoStockDenied("FORBIDDEN")
        old=conn.execute("""SELECT id,request_id,created_by,canceled_remainder
          FROM auto_supplier_purchase_cancel WHERE purchase_id=%s""",
          (purchase,)).fetchone()
        if old:
            if old[1]==request_id and old[2]==actor:
                return old[0],old[3]
            raise AutoStockConflict("ALREADY_CANCELED")
        delivered=conn.execute("""SELECT COALESCE(SUM(quantity),0)::bigint
          FROM auto_supplier_purchase_receipt WHERE purchase_id=%s""",
          (purchase,)).fetchone()[0]
        remaining=row[1]-delivered
        if remaining<=0:
            raise AutoStockConflict("NOTHING_LEFT")
        def insert():
            ident=uuid4()
            conn.execute("""INSERT INTO auto_supplier_purchase_cancel(
              id,request_id,purchase_id,canceled_remainder,created_by)
              VALUES(%s,%s,%s,%s,%s)""",(ident,request_id,purchase,remaining,actor))
            return ident,remaining
        return _conflict_errors((conn,actor,insert))


def list_supplier_orders(conn: psycopg.Connection, token: str, org: UUID, loc: UUID,
                         limit: int=20, after_id: UUID|None=None) -> tuple[list[dict],UUID|None]:
    if (not _valid_ids(org,loc) or type(limit) is not int or not 1<=limit<=50 or
        (after_id is not None and type(after_id) is not UUID)):
        raise AutoStockDenied("INVALID_INPUT")
    with conn.transaction():
        try:
            cutoff=require_document_access(conn,token,org,loc,
                ("WAREHOUSE","MANAGER"),"SUPPLIER_ORDER","READ")
        except DocumentPolicyDenied:
            raise AutoStockDenied("FORBIDDEN") from None
        price_visible=has_document_access(conn,token,org,loc,
            ("WAREHOUSE","MANAGER"),"SUPPLIER_ORDER","PRICE_PURCHASE")
        rows=conn.execute("""SELECT p.id,p.work_order_id,p.lot_id,s.article,
             p.ordered_quantity,p.selected_supplier,p.selected_offer,
             p.quote_revision,p.purchase_unit_minor,p.currency,
             COALESCE((SELECT SUM(r.quantity) FROM auto_supplier_purchase_receipt r
                         WHERE r.purchase_id=p.id),0)::bigint AS received,
             COALESCE((SELECT SUM(r.quantity)
                 FROM auto_supplier_purchase_receipt r
                 JOIN auto_stock_receipt_acceptance a ON a.document_id=r.document_id
                 WHERE r.purchase_id=p.id),0)::bigint AS accepted,
             COALESCE((SELECT SUM(r.quantity)
                 FROM auto_supplier_purchase_receipt r
                 JOIN auto_stock_receipt_valuation v ON v.document_id=r.document_id
                 WHERE r.purchase_id=p.id),0)::bigint AS valued,
             c.canceled_remainder
          FROM auto_supplier_purchase_order p
          JOIN auto_stock_sku s ON s.id=p.sku_id
          JOIN auto_organization o ON o.id=p.organization_id
          JOIN auto_service_location l ON l.id=p.location_id
            AND l.organization_id=p.organization_id
          LEFT JOIN auto_supplier_purchase_cancel c ON c.purchase_id=p.id
          WHERE p.organization_id=%s AND p.location_id=%s
            AND o.disabled_at IS NULL AND l.disabled_at IS NULL
            AND (%s::uuid IS NULL OR p.id>%s::uuid)
            AND (%s::timestamptz IS NULL OR p.created_at >= %s::timestamptz)
          ORDER BY p.id LIMIT %s""",
          (org,loc,after_id,after_id,cutoff,cutoff,limit+1)).fetchall()
        nxt=rows[limit-1][0] if len(rows)>limit else None
        return ([{
            "orderId":str(i),"workOrderId":str(w),"lotId":str(lot),
            "article":art,"orderedQuantity":qty,"supplierId":supplier,
            "offerId":offer,"quoteRevision":rev,
            "purchaseUnitMinor":unit if price_visible else None,
            "purchasePriceHidden":not price_visible,
            "currency":currency,"documentedQuantity":documented,
            "acceptedQuantity":accepted,"valuedQuantity":valued,
            "canceledQuantity":cancel or 0,
            "outstandingQuantity":qty-documented-(cancel or 0),
         } for (i,w,lot,art,qty,supplier,offer,rev,unit,currency,
                documented,accepted,valued,cancel) in rows[:limit]],nxt)
