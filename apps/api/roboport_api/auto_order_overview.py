"""Synthetic joint order summary with signed, tenant-bound stock pagination."""
from uuid import UUID
import hashlib
import psycopg
from .auto_order_customer_read import get_order_customer_summary
from .auto_order_stock_read import list_work_order_stock
from .auto_stock_service import AutoStockDenied
from .keyset_cursor import encode_cursor, decode_cursor


def get_order_overview(conn: psycopg.Connection, token: str,
                       organization_id: UUID, location_id: UUID,
                       order_id: UUID, *, limit: int = 50,
                       cursor: str | None = None) -> dict:
    """Read two guarded domains in one transaction without partial results.

    The warehouse service verifies live WAREHOUSE/MANAGER authority and retains
    its transaction locks. The page query uses the same tenant, location, order,
    and SKU predicates as the existing warehouse read; no offset-based drift.
    """
    if type(token) is not str or not token.strip():
        raise AutoStockDenied("FORBIDDEN")
    if (any(type(x) is not UUID for x in (organization_id, location_id, order_id))
            or type(limit) is not int or not 1 <= limit <= 100
            or (cursor is not None and (type(cursor) is not str or not cursor))):
        raise AutoStockDenied("INVALID_INPUT")

    scope_identity = f"auto-stock:{organization_id}:{location_id}:{order_id}"
    # Keep the authenticated scope inside the signed cursor size bound (256).
    scope = hashlib.sha256(scope_identity.encode("ascii")).hexdigest()[:32]
    position = None
    if cursor is not None:
        try:
            position = decode_cursor(cursor, scope, token)
        except ValueError as exc:
            raise AutoStockDenied("INVALID_INPUT") from exc

    with conn.transaction():
        customer = get_order_customer_summary(conn, token, organization_id,
                                              location_id, order_id)
        # Require both domain permissions for every page, even an empty one.
        list_work_order_stock(conn, token, organization_id, location_id,
                              order_id, limit=1)
        predicate = ""
        params = [organization_id, location_id, order_id]
        if position is not None:
            predicate = " AND (e.created_at,e.stock_event_id) < (%s,%s)"
            params.extend(position)
        params.append(limit + 1)
        rows = conn.execute(
            """SELECT e.stock_event_id,e.sku_id,s.article,e.action,e.quantity,e.created_at
               FROM auto_work_order_stock_event e
               JOIN auto_stock_sku s ON s.id=e.sku_id
                 AND s.organization_id=%s AND s.location_id=%s
               WHERE e.work_order_id=%s""" + predicate +
            """ ORDER BY e.created_at DESC,e.stock_event_id DESC LIMIT %s""",
            tuple(params),
        ).fetchall()
        has_more = len(rows) > limit
        page = rows[:limit]
        next_cursor = None
        if has_more:
            last = page[-1]
            next_cursor = encode_cursor(last[5], last[0], scope, token)
        stock = [{"eventId": str(event), "skuId": str(sku),
                  "article": article, "action": action, "quantity": qty,
                  "createdAt": stamp.isoformat()}
                 for event, sku, article, action, qty, stamp in page]
        return {"customer": customer, "stock": {"items": stock,
                                                 "nextCursor": next_cursor}}
