"""Live warehouse-grant per-unit serial issue, return, and branch transfer.

The original serial registration remains immutable; movements link exactly one
physical lot/work-order/interbranch ledger unit in the same PG transaction.
"""
from uuid import UUID
import psycopg
from .auto_org_authority import organization_role_allows
from .auto_purchase_document_policy import require_full_stock_history
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
        # Check source scope before looking up a replay or serial identity.
        # A denied actor must not distinguish an existing conflicting key.
        if not organization_role_allows(conn,token,organization_id,
                "WAREHOUSE",location_id,for_mutation=True):
            raise AutoStockDenied("FORBIDDEN")
        require_full_stock_history(conn,token,organization_id,location_id,"EDIT")
        # A transfer changes the current branch. Replays must still verify original
        # branch and both live grants, without attempting a second physical transfer.
        prior=conn.execute("""SELECT m.id,m.serial_id,m.action,m.work_order_id,
                   m.destination_lot_id,m.performed_by,src.organization_id,
                   src.location_id,dst.location_id
                FROM auto_stock_serial_movement m
                JOIN auto_stock_lot src ON src.id=m.source_lot_id
                LEFT JOIN auto_stock_lot dst ON dst.id=m.destination_lot_id
                WHERE m.request_id=%s""",(request_id,)).fetchone()
        if prior is not None:
            if prior[6]!=organization_id or prior[7]!=location_id:
                raise AutoStockDenied("FORBIDDEN")
            for loc in sorted({v for v in (prior[7],prior[8]) if v is not None},key=str):
                if not organization_role_allows(conn,token,organization_id,
                     "WAREHOUSE",loc,for_mutation=True):
                    raise AutoStockDenied("FORBIDDEN")
                require_full_stock_history(conn,token,organization_id,loc,"EDIT")
            if session_subject(conn,token,for_mutation=True)!=prior[5]:
                raise AutoStockDenied("FORBIDDEN")
            if (prior[1]!=serial_id or prior[2]!=action
                or prior[3]!=work_order_id or prior[4]!=destination_lot_id):
                raise AutoStockConflict("CONFLICT")
            return prior[0]
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
        for loc in sorted({source[2]} if dest is None else {source[2],dest[1]},key=str):
            if not organization_role_allows(conn,token,organization_id,
                    "WAREHOUSE",loc,for_mutation=True):
                raise AutoStockDenied("FORBIDDEN")
            require_full_stock_history(conn,token,organization_id,loc,"EDIT")
        ids=[source[1]] if dest is None else [source[1],dest[0]]
        conn.execute("""SELECT id FROM auto_stock_sku WHERE id=ANY(%s::uuid[])
            ORDER BY id FOR UPDATE""",(ids,)).fetchall()
        still=conn.execute("""SELECT lot_id,sku_id,location_id,state
          FROM auto_stock_serial_current WHERE serial_id=%s AND organization_id=%s""",
          (serial_id,organization_id)).fetchone()
        # Исторический exact replay остаётся допустимым для ISSUE/RETURN
        # после конкурентного изменения IN_STOCK ↔ ISSUED: SQL проверяет ключ.
        # Терминальное выбытие не может породить новую операцию движения.
        if still is None or tuple(still[:3])!=tuple(source[:3]) or still[3] not in ("IN_STOCK","ISSUED"):
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
