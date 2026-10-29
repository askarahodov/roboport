"""Synthetic internal work-order stock workflow; no HTTP endpoints."""
from uuid import UUID
import psycopg

from .auto_org_authority import organization_role_allows
from .auto_stock_service import AutoStockDenied, AutoStockConflict


def mutate_work_order_stock(
    conn: psycopg.Connection, token: str, organization_id: UUID,
    location_id: UUID, work_order_id: UUID, sku_id: UUID,
    request_id: UUID, action: str, quantity: int,
) -> UUID:
    if (type(token) is not str or not token
        or not all(isinstance(x, UUID) for x in
                   (organization_id, location_id, work_order_id, sku_id, request_id))
        or action not in ("RESERVE", "ISSUE", "RELEASE") or type(action) is not str
        or type(quantity) is not int or not 1 <= quantity <= 1_000_000_000):
        raise AutoStockDenied("INVALID_INPUT")
    with conn.transaction():
        if not organization_role_allows(
            conn, token, organization_id, "WAREHOUSE", location_id,
            for_mutation=True,
        ):
            raise AutoStockDenied("FORBIDDEN")
        # Check both objects before invoking privileged DB operations; a
        # caller knowing a different organization's UUID must not consume it.
        objects = conn.execute(
            """SELECT w.id FROM auto_work_order w
               JOIN auto_stock_sku s ON s.organization_id=w.organization_id
                 AND s.location_id=w.location_id
               WHERE w.id=%s AND s.id=%s AND w.organization_id=%s
                 AND w.location_id=%s FOR SHARE OF w,s""",
            (work_order_id, sku_id, organization_id, location_id),
        ).fetchone()
        if objects is None:
            raise AutoStockDenied("FORBIDDEN")
        try:
            with conn.transaction():
                event = conn.execute(
                    "SELECT record_auto_work_order_stock(%s,%s,%s,%s,%s)",
                    (work_order_id, sku_id, request_id, action, quantity),
                ).fetchone()
            return event[0]
        except (psycopg.errors.RaiseException, psycopg.errors.UniqueViolation,
                psycopg.errors.CheckViolation):
            raise AutoStockConflict("CONFLICT") from None
