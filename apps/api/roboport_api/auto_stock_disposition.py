"""Protected internal disposition: write-off or supplier stock return."""
from uuid import UUID
import psycopg
from .auto_org_authority import organization_role_allows
from .auto_stock_service import AutoStockDenied,AutoStockConflict


def dispose_stock_lot(conn: psycopg.Connection, token: str,
    organization_id: UUID, location_id: UUID, lot_id: UUID,
    request_id: UUID, action: str, quantity: int, reason: str,
    supplier_reference: str | None = None) -> UUID:
    if (type(token) is not str or not token
        or not all(isinstance(x,UUID) for x in
                   (organization_id,location_id,lot_id,request_id))
        or action not in ("WRITE_OFF","SUPPLIER_RETURN")
        or type(quantity) is not int or not 1<=quantity<=1_000_000_000
        or type(reason) is not str or not 3<=len(reason.strip())<=240
        or (action=="SUPPLIER_RETURN" and (type(supplier_reference) is not str
             or not 3<=len(supplier_reference.strip())<=160))
        or (action=="WRITE_OFF" and supplier_reference is not None)):
        raise AutoStockDenied("INVALID_INPUT")
    with conn.transaction():
        if not organization_role_allows(conn,token,organization_id,
                "WAREHOUSE",location_id,for_mutation=True):
            raise AutoStockDenied("FORBIDDEN")
        found=conn.execute("""SELECT id FROM auto_stock_lot
             WHERE id=%s AND organization_id=%s AND location_id=%s
             FOR SHARE""",(lot_id,organization_id,location_id)).fetchone()
        if found is None:
            raise AutoStockDenied("FORBIDDEN")
        try:
            with conn.transaction():
                result=conn.execute("""
                    SELECT record_auto_stock_lot_disposition(%s,%s,%s,%s,%s,%s)
                """,(lot_id,request_id,action,quantity,reason,supplier_reference)).fetchone()[0]
                conn.execute("SET CONSTRAINTS auto_stock_serial_allocation_guard IMMEDIATE")
                conn.execute("SET CONSTRAINTS auto_stock_serial_allocation_guard DEFERRED")
                return result
        except (psycopg.errors.RaiseException,psycopg.errors.UniqueViolation,
                psycopg.errors.CheckViolation):
            raise AutoStockConflict("CONFLICT") from None
