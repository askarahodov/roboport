"""Two-person warehouse receiving, scoped to a live organization and branch."""
from uuid import UUID
import psycopg
from .auto_org_authority import organization_role_allows
from .auto_stock_service import AutoStockDenied,AutoStockConflict
from .session_lookup import session_subject


def _invoke(conn,token,organization_id,location_id,lot_id,role,sql,args):
    with conn.transaction():
        if not organization_role_allows(conn,token,organization_id,role,
                                         location_id,for_mutation=True):
            raise AutoStockDenied("FORBIDDEN")
        if conn.execute("""SELECT id FROM auto_stock_lot
             WHERE id=%s AND organization_id=%s AND location_id=%s FOR SHARE""",
             (lot_id,organization_id,location_id)).fetchone() is None:
            raise AutoStockDenied("FORBIDDEN")
        actor=session_subject(conn,token,for_mutation=True)
        if actor is None:
            raise AutoStockDenied("FORBIDDEN")
        try:
            with conn.transaction():
                conn.execute("SELECT set_config('roboport.receipt_actor',%s,true)",
                             (str(actor),))
                return conn.execute(sql,args).fetchone()[0]
        except (psycopg.errors.RaiseException,psycopg.errors.UniqueViolation,
                psycopg.errors.CheckViolation):
            raise AutoStockConflict("CONFLICT") from None
        finally:
            conn.execute("SELECT set_config('roboport.receipt_actor','',true)")


def create_receipt_document(conn: psycopg.Connection,token: str,organization_id: UUID,
     location_id: UUID,lot_id: UUID,request_id: UUID,quantity: int,
     source_reference: str) -> UUID:
    if (type(token) is not str or not token or
        not all(isinstance(v,UUID) for v in
        (organization_id,location_id,lot_id,request_id)) or
        type(quantity) is not int or not 1<=quantity<=1_000_000_000 or
        type(source_reference) is not str or
        not 3<=len(source_reference.strip())<=160):
        raise AutoStockDenied("INVALID_INPUT")
    return _invoke(conn,token,organization_id,location_id,lot_id,"WAREHOUSE",
        "SELECT record_auto_stock_receipt_document(%s,%s,%s,%s)",
        (lot_id,request_id,quantity,source_reference))


def accept_receipt_document(conn: psycopg.Connection,token: str,organization_id: UUID,
     location_id: UUID,document_id: UUID,request_id: UUID) -> UUID:
    if (type(token) is not str or not token or
        not all(isinstance(v,UUID) for v in
        (organization_id,location_id,document_id,request_id))):
        raise AutoStockDenied("INVALID_INPUT")
    with conn.transaction():
        if not organization_role_allows(conn,token,organization_id,"MANAGER",
                                         location_id,for_mutation=True):
            raise AutoStockDenied("FORBIDDEN")
        row=conn.execute("""SELECT d.lot_id FROM auto_stock_receipt_document d
            WHERE d.id=%s AND d.organization_id=%s AND d.location_id=%s
            FOR SHARE""",(document_id,organization_id,location_id)).fetchone()
        if row is None:
            raise AutoStockDenied("FORBIDDEN")
        return _invoke(conn,token,organization_id,location_id,row[0],"MANAGER",
            "SELECT accept_auto_stock_receipt_document(%s,%s)",
            (document_id,request_id))
