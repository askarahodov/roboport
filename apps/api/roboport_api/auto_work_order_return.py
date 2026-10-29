"""Authorized synthetic stock return for one automobile work order."""
from uuid import UUID
import psycopg

from .auto_org_authority import organization_role_allows
from .auto_stock_service import AutoStockDenied, AutoStockConflict


def return_work_order_stock(
    conn: psycopg.Connection, token: str, organization_id: UUID,
    location_id: UUID, work_order_id: UUID, sku_id: UUID,
    request_id: UUID, quantity: int,
) -> UUID:
    if (type(token) is not str or not token
        or not all(isinstance(x, UUID) for x in
                   (organization_id, location_id, work_order_id, sku_id, request_id))
        or type(quantity) is not int or not 1 <= quantity <= 1_000_000_000):
        raise AutoStockDenied("INVALID_INPUT")
    with conn.transaction():
        if not organization_role_allows(
            conn, token, organization_id, "WAREHOUSE", location_id,
            for_mutation=True,
        ):
            raise AutoStockDenied("FORBIDDEN")
        row = conn.execute(
            """SELECT w.id FROM auto_work_order w JOIN auto_stock_sku s
               ON s.organization_id=w.organization_id
                 AND s.location_id=w.location_id
               WHERE w.id=%s AND s.id=%s AND w.organization_id=%s
                 AND w.location_id=%s FOR SHARE OF w,s""",
            (work_order_id, sku_id, organization_id, location_id),
        ).fetchone()
        if row is None:
            raise AutoStockDenied("FORBIDDEN")
        try:
            with conn.transaction():
                result = conn.execute(
                    "SELECT record_auto_work_order_return(%s,%s,%s,%s)",
                    (work_order_id, sku_id, request_id, quantity),
                ).fetchone()
                # Force deferred serial conservation inside the savepoint so
                # a normal conflict is translated to 409, not HTTP 500 at COMMIT.
                conn.execute("SET CONSTRAINTS auto_stock_serial_sku_return_guard IMMEDIATE")
                conn.execute("SET CONSTRAINTS auto_stock_serial_sku_return_guard DEFERRED")
            return result[0]
        except (psycopg.errors.RaiseException, psycopg.errors.UniqueViolation,
                psycopg.errors.CheckViolation):
            raise AutoStockConflict("CONFLICT") from None
