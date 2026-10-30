"""Synthetic authorized WorkOrder charge/credit posting, never a payment."""
from uuid import UUID
import psycopg
from .auto_order_financial_read import get_work_order_balance
from .auto_stock_service import AutoStockDenied, AutoStockConflict


def post_work_order_financial(conn: psycopg.Connection, token: str,
                              organization: UUID, location: UUID, order: UUID,
                              key: UUID, action: str, amount_minor: int,
                              reason: str) -> UUID:
    if (any(type(x) is not UUID for x in (organization, location, order, key))
            or action not in ("CHARGE", "CREDIT")
            or type(amount_minor) is not int or not 1 <= amount_minor <= 1000000000000
            or type(reason) is not str or not 3 <= len(reason.strip()) <= 160):
        raise AutoStockDenied("INVALID_INPUT")
    with conn.transaction():
        # Same live manager/verified customer proof as the sensitive read API.
        # Keep the permission locks until the posting commits.
        get_work_order_balance(conn, token, organization, location, order)
        try:
            with conn.transaction():
                result = conn.execute(
                    "SELECT record_auto_work_order_financial(%s,%s,%s,%s,%s)",
                    (order, key, action, amount_minor, reason),
                ).fetchone()
        except (psycopg.errors.RaiseException, psycopg.errors.UniqueViolation,
                psycopg.errors.CheckViolation) as exc:
            raise AutoStockConflict("CONFLICT") from exc
        return result[0]
