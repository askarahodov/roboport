"""Manager-only synthetic WorkOrder balance; no payment or fiscal permissions."""
from uuid import UUID
import hashlib
from .keyset_cursor import encode_cursor, decode_cursor
import psycopg
from .auto_org_authority import organization_role_allows
from .auto_order_customer_read import get_order_customer_summary
from .auto_stock_service import AutoStockDenied


def get_work_order_balance(conn: psycopg.Connection, token: str,
                           organization_id: UUID, location_id: UUID,
                           order_id: UUID) -> dict:
    if type(token) is not str or not token.strip():
        raise AutoStockDenied("FORBIDDEN")
    if any(type(value) is not UUID
           for value in (organization_id, location_id, order_id)):
        raise AutoStockDenied("INVALID_INPUT")
    with conn.transaction():
        if not organization_role_allows(conn, token, organization_id, "MANAGER",
                                        location_id, for_mutation=True):
            raise AutoStockDenied("FORBIDDEN")
        # Live customer/vehicle proof is mandatory, even for a manager.
        get_order_customer_summary(conn, token, organization_id, location_id, order_id)
        row = conn.execute(
            """SELECT balance_minor FROM auto_work_order_financial_balance
               WHERE work_order_id=%s AND organization_id=%s AND location_id=%s""",
            (order_id, organization_id, location_id),
        ).fetchone()
        if row is None:
            raise AutoStockDenied("FORBIDDEN")
        return {"orderId": str(order_id), "balanceMinor": row[0],
                "currency": None, "syntheticOnly": True}


def list_work_order_financial_events(conn: psycopg.Connection, token: str,
                                     organization_id: UUID, location_id: UUID,
                                     order_id: UUID, *, limit: int = 50,
                                     cursor: str | None = None) -> dict:
    """Read bounded append-only financial history after live MANAGER/ownership proof."""
    if type(limit) is not int or not 1 <= limit <= 100:
        raise AutoStockDenied("INVALID_INPUT")
    if cursor is not None and (type(cursor) is not str or not cursor):
        raise AutoStockDenied("INVALID_INPUT")
    scope = hashlib.sha256(
        f"auto-financial:{organization_id}:{location_id}:{order_id}".encode("ascii")
    ).hexdigest()[:32]
    try:
        position = decode_cursor(cursor, scope, token) if cursor is not None else None
    except ValueError as exc:
        raise AutoStockDenied("INVALID_INPUT") from exc
    with conn.transaction():
        balance = get_work_order_balance(conn, token, organization_id, location_id, order_id)
        predicate = " AND (created_at,id) < (%s,%s)" if position is not None else ""
        params = [order_id, organization_id, location_id]
        if position is not None:
            params.extend(position)
        params.append(limit + 1)
        rows = conn.execute(
            """SELECT id,action,amount_minor,reason,created_at
               FROM auto_work_order_financial_event
               WHERE work_order_id=%s AND organization_id=%s AND location_id=%s""" + predicate +
            """ ORDER BY created_at DESC,id DESC LIMIT %s""",
            tuple(params),
        ).fetchall()
        has_more = len(rows) > limit
        page = rows[:limit]
        next_cursor = encode_cursor(page[-1][4], page[-1][0], scope, token) if has_more else None
        return {
            "orderId": balance["orderId"],
            "balanceMinor": balance["balanceMinor"],
            "currency": None,
            "syntheticOnly": True,
            "items": [
                {"id": str(identifier), "action": action, "amountMinor": amount,
                 "reason": reason, "createdAt": stamp.isoformat()}
                for identifier, action, amount, reason, stamp in page
            ],
            "nextCursor": next_cursor,
        }
