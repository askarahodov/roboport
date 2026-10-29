"""Live warehouse-grant per-unit serial issue, return, and branch transfer.

The original serial registration remains immutable; movements link exactly one
physical lot/work-order/interbranch ledger unit in the same PG transaction.
"""
from uuid import UUID
import psycopg
from .auto_org_authority import organization_role_allows
from .session_lookup import session_subject
from .auto_stock_service import AutoStockDenied,AutoStockConflict


def move_lot_serial(conn: psycopg.Connection, token: str,
        organization_id: UUID, location_id: UUID, serial_id: UUID, request_id: UUID, action: str,
        work_order_id: UUID | None = None, destination_lot_id: UUID | None = None) -> UUID:
    if (type(token) is not str or not token
        or not all(isinstance(v,UUID) for v in (organization_id,location_id,serial_id,request_id))
        or action not in ("ISSUE","RETURN","TRANSFER")
        or (action in ("ISSUE","RETURN") and (
            not isinstance(work_order_id,UUID) or destination_lot_id is not None))
        or (action=="TRANSFER" and (
            not isinstance(destination_lot_id,UUID) or work_order_id is not None))):
        raise AutoStockDenied("INVALID_INPUT")
    with conn.transaction():
        source=conn.execute("""SELECT lot_id,sku_id,location_id,state
           FROM auto_stock_serial_current WHERE serial_id=%s AND organization_id=%s""",
           (serial_id,organization_id)).fetchone()
        if source is None or source[2]!=location_id:
            raise AutoStockDenied("FORBIDDEN")
        dest=None
        if action=="TRANSFER":
            dest=conn.execute("""SELECT sku_id,location_id FROM auto_stock_lot
               WHERE id=%s AND organization_id=%s""",
               (destination_lot_id,organization_id)).fetchone()
            if dest is None or dest[1]==source[2]:
                raise AutoStockDenied("FORBIDDEN")
        for loc in (source[2],) if dest is None else (source[2],dest[1]):
            if not organization_role_allows(conn,token,organization_id,
                    "WAREHOUSE",loc,for_mutation=True):
                raise AutoStockDenied("FORBIDDEN")
        ids=[source[1]] if dest is None else [source[1],dest[0]]
        conn.execute("""SELECT id FROM auto_stock_sku WHERE id=ANY(%s::uuid[])
            ORDER BY id FOR UPDATE""",(ids,)).fetchall()
        still=conn.execute("""SELECT lot_id,sku_id,location_id
          FROM auto_stock_serial_current WHERE serial_id=%s AND organization_id=%s""",
          (serial_id,organization_id)).fetchone()
        if still is None or tuple(still)!=tuple(source[:3]):
            raise AutoStockConflict("CONFLICT")
        actor=session_subject(conn,token,for_mutation=True)
        if actor is None:
            raise AutoStockDenied("FORBIDDEN")
        try:
            with conn.transaction():
                conn.execute("SELECT set_config('roboport.serial_actor',%s,true)",
                            (str(actor),))
                return conn.execute(
                  "SELECT record_auto_stock_serial_movement(%s,%s,%s,%s,%s)",
                  (serial_id,request_id,action,work_order_id,destination_lot_id)).fetchone()[0]
        except (psycopg.errors.RaiseException,psycopg.errors.UniqueViolation,
                psycopg.errors.CheckViolation,psycopg.errors.ForeignKeyViolation):
            raise AutoStockConflict("CONFLICT") from None
        finally:
            conn.execute("SELECT set_config('roboport.serial_actor','',true)")
