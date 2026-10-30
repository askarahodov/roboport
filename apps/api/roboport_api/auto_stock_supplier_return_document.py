"""Accepted-receipt supplier returns with independently authorized warehouse/manager steps.

Internal synthetic-only: purchase cost/VAT attribution is not a legal credit note.
"""
from uuid import UUID
import psycopg
from .auto_org_authority import organization_role_allows
from .session_lookup import session_subject
from .auto_stock_service import AutoStockConflict, AutoStockDenied

SQL_FAILURES=(psycopg.errors.RaiseException,psycopg.errors.UniqueViolation,
              psycopg.errors.CheckViolation,psycopg.errors.ForeignKeyViolation)


def _validate(token,*ids):
    if type(token) is not str or not token or any(type(i) is not UUID for i in ids):
        raise AutoStockDenied("INVALID_INPUT")


def _run(conn,token,org,loc,role,sql,args):
    with conn.transaction():
        if not organization_role_allows(conn,token,org,role,loc,for_mutation=True):
            raise AutoStockDenied("FORBIDDEN")
        actor=session_subject(conn,token,for_mutation=True)
        if actor is None: raise AutoStockDenied("FORBIDDEN")
        try:
            with conn.transaction():
                conn.execute("SELECT set_config('roboport.supplier_return_actor',%s,true)",
                             (str(actor),))
                ident=conn.execute(sql,args).fetchone()[0]
                # Enforce both physical and serial deferred constraints in a
                # protected savepoint before the outer HTTP connection commits.
                conn.execute("SET CONSTRAINTS auto_stock_supplier_return_pair IMMEDIATE")
                conn.execute("SET CONSTRAINTS auto_stock_supplier_return_pair DEFERRED")
                conn.execute("SET CONSTRAINTS auto_stock_serial_allocation_guard IMMEDIATE")
                conn.execute("SET CONSTRAINTS auto_stock_serial_allocation_guard DEFERRED")
                return ident
        except SQL_FAILURES:
            raise AutoStockConflict("CONFLICT") from None
        finally:
            conn.execute("SELECT set_config('roboport.supplier_return_actor','',true)")


def draft_supplier_return(conn:psycopg.Connection,token:str,
    org:UUID,loc:UUID,receipt_id:UUID,request_id:UUID,
    quantity:int,reason:str,supplier_reference:str)->UUID:
    _validate(token,org,loc,receipt_id,request_id)
    if (type(quantity) is not int or not 1<=quantity<=1_000_000_000
      or type(reason) is not str or not 3<=len(reason)<=240
      or reason!=reason.strip()
      or type(supplier_reference) is not str
      or not 3<=len(supplier_reference)<=160
      or supplier_reference!=supplier_reference.strip()):
        raise AutoStockDenied("INVALID_INPUT")
    with conn.transaction():
        row=conn.execute("""SELECT id FROM auto_stock_receipt_document
            WHERE id=%s AND organization_id=%s AND location_id=%s FOR SHARE""",
            (receipt_id,org,loc)).fetchone()
        if row is None: raise AutoStockDenied("FORBIDDEN")
        return _run(conn,token,org,loc,"WAREHOUSE",
            "SELECT record_auto_stock_supplier_return_document(%s,%s,%s,%s,%s)",
            (receipt_id,request_id,quantity,reason,supplier_reference))


def approve_supplier_return(conn:psycopg.Connection,token:str,
    org:UUID,loc:UUID,document_id:UUID,request_id:UUID)->UUID:
    _validate(token,org,loc,document_id,request_id)
    with conn.transaction():
        row=conn.execute("""SELECT id FROM auto_stock_supplier_return_document
            WHERE id=%s AND organization_id=%s AND location_id=%s FOR SHARE""",
            (document_id,org,loc)).fetchone()
        if row is None: raise AutoStockDenied("FORBIDDEN")
        return _run(conn,token,org,loc,"MANAGER",
            "SELECT accept_auto_stock_supplier_return_document(%s,%s)",
            (document_id,request_id))
