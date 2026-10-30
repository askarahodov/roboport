"""Synthetic joint order summary: both customer and warehouse authority required."""
from uuid import UUID
import psycopg
from .auto_order_customer_read import get_order_customer_summary
from .auto_order_stock_read import list_work_order_stock
from .auto_stock_service import AutoStockDenied


def get_order_overview(conn: psycopg.Connection, token: str,
                       organization_id: UUID, location_id: UUID,
                       order_id: UUID, *, limit: int = 50) -> dict:
    """Read both domains under one transaction; never return partial results.

    Nested service transactions remain savepoints under the enclosing transaction,
    retaining the access and resource locks until both reads have completed.
    """
    if type(token) is not str or not token.strip():
        raise AutoStockDenied("FORBIDDEN")
    if (any(type(x) is not UUID for x in (organization_id, location_id, order_id))
            or type(limit) is not int or not 1 <= limit <= 100):
        raise AutoStockDenied("INVALID_INPUT")
    with conn.transaction():
        customer = get_order_customer_summary(conn, token, organization_id,
                                              location_id, order_id)
        stock = list_work_order_stock(conn, token, organization_id,
                                      location_id, order_id, limit=limit)
        return {"customer": customer, "stock": {"items": stock}}
