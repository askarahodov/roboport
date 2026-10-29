"""Live WAREHOUSE authority for synthetic lot allocation operations."""
from uuid import UUID
import psycopg
from .auto_org_authority import organization_role_allows
from .auto_stock_service import AutoStockDenied, AutoStockConflict


def mutate_stock_lot_allocation(conn: psycopg.Connection, token: str,
    organization_id: UUID, location_id: UUID, lot_id: UUID,
    request_id: UUID, action: str, quantity: int) -> UUID:
    if (type(token) is not str or not token
        or not all(isinstance(v,UUID) for v in
                   (organization_id,location_id,lot_id,request_id))
        or type(action) is not str or action not in ("ALLOCATE","DEALLOCATE")
        or type(quantity) is not int or not 1<=quantity<=1_000_000_000):
        raise AutoStockDenied("INVALID_INPUT")
    with conn.transaction():
        if not organization_role_allows(conn,token,organization_id,"WAREHOUSE",
                                        location_id,for_mutation=True):
            raise AutoStockDenied("FORBIDDEN")
        lot=conn.execute("""SELECT id FROM auto_stock_lot
             WHERE id=%s AND organization_id=%s AND location_id=%s FOR SHARE""",
             (lot_id,organization_id,location_id)).fetchone()
        if lot is None:
            raise AutoStockDenied("FORBIDDEN")
        try:
            with conn.transaction():
                row=conn.execute(
                    "SELECT record_auto_stock_lot_allocation(%s,%s,%s,%s)",
                    (lot_id,request_id,action,quantity)).fetchone()
            return row[0]
        except (psycopg.errors.RaiseException,psycopg.errors.UniqueViolation,
                psycopg.errors.CheckViolation):
            raise AutoStockConflict("CONFLICT") from None
