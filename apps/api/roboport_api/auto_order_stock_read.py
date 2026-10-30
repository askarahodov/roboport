"""Tenant- and branch-scoped work order stock history, synthetic dev only."""
from uuid import UUID
import psycopg
from .auto_org_authority import organization_role_allows
from .auto_stock_service import AutoStockDenied


def list_work_order_stock(conn: psycopg.Connection, token: str,
                          organization_id: UUID, location_id: UUID,
                          work_order_id: UUID, *, limit: int = 50) -> list[dict]:
    if type(token) is not str or not token.strip():
        raise AutoStockDenied("FORBIDDEN")
    if (any(type(v) is not UUID for v in (organization_id, location_id, work_order_id))
            or type(limit) is not int or not 1 <= limit <= 100):
        raise AutoStockDenied("INVALID_INPUT")
    with conn.transaction():
        if not (organization_role_allows(conn, token, organization_id, "WAREHOUSE",
                                         location_id, for_mutation=True)
                or organization_role_allows(conn, token, organization_id, "MANAGER",
                                            location_id, for_mutation=True)):
            raise AutoStockDenied("FORBIDDEN")
        order = conn.execute(
            """SELECT w.id FROM auto_work_order w
               JOIN auto_organization o ON o.id=w.organization_id AND o.disabled_at IS NULL
               JOIN auto_service_location l ON l.id=w.location_id
                 AND l.organization_id=o.id AND l.disabled_at IS NULL
               WHERE w.id=%s AND w.organization_id=%s AND w.location_id=%s
               FOR SHARE OF w,o,l""",
            (work_order_id, organization_id, location_id),
        ).fetchone()
        if order is None:
            raise AutoStockDenied("FORBIDDEN")
        rows = conn.execute(
            """SELECT e.stock_event_id,e.sku_id,s.article,e.action,e.quantity,e.created_at
               FROM auto_work_order_stock_event e
               JOIN auto_stock_sku s ON s.id=e.sku_id
                 AND s.organization_id=%s AND s.location_id=%s
               WHERE e.work_order_id=%s
               ORDER BY e.created_at DESC,e.stock_event_id DESC LIMIT %s""",
            (organization_id, location_id, work_order_id, limit),
        ).fetchall()
        return [{"eventId":str(eid), "skuId":str(sku), "article":article,
                 "action":action, "quantity":qty, "createdAt":stamp.isoformat()}
                for eid,sku,article,action,qty,stamp in rows]
