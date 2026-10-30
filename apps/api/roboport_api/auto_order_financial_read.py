"""Manager-only synthetic WorkOrder balance; no payment or fiscal permissions."""
from uuid import UUID
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
