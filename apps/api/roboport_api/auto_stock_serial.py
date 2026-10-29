"""Live warehouse-grant serial registration against a specific scoped lot."""
from uuid import UUID
import psycopg
from .auto_org_authority import organization_role_allows
from .session_lookup import session_subject
from .auto_stock_service import AutoStockDenied,AutoStockConflict


def register_lot_serial(conn: psycopg.Connection, token: str,
    organization_id: UUID,location_id: UUID,lot_id: UUID,
    request_id: UUID,serial_number: str) -> UUID:
    if (type(token) is not str or not token
        or not all(isinstance(v,UUID) for v in
                   (organization_id,location_id,lot_id,request_id))
        or type(serial_number) is not str
        or not 3<=len(serial_number)<=120
        or serial_number!=serial_number.strip()):
        raise AutoStockDenied("INVALID_INPUT")
    with conn.transaction():
        if not organization_role_allows(conn,token,organization_id,
                "WAREHOUSE",location_id,for_mutation=True):
            raise AutoStockDenied("FORBIDDEN")
        lot=conn.execute("""SELECT id FROM auto_stock_lot WHERE id=%s
          AND organization_id=%s AND location_id=%s FOR SHARE""",
          (lot_id,organization_id,location_id)).fetchone()
        if lot is None: raise AutoStockDenied("FORBIDDEN")
        actor=session_subject(conn,token,for_mutation=True)
        if actor is None: raise AutoStockDenied("FORBIDDEN")
        try:
            with conn.transaction():
                conn.execute("SELECT set_config('roboport.serial_actor',%s,true)",
                             (str(actor),))
                return conn.execute("SELECT register_auto_stock_serial(%s,%s,%s)",
                                    (lot_id,request_id,serial_number)).fetchone()[0]
        except (psycopg.errors.RaiseException,psycopg.errors.UniqueViolation,
                psycopg.errors.CheckViolation):
            raise AutoStockConflict("CONFLICT") from None
        finally:
            conn.execute("SELECT set_config('roboport.serial_actor','',true)")
