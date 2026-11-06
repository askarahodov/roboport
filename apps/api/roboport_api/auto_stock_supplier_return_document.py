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



def pending_supplier_returns(conn:psycopg.Connection,token:str,org:UUID,loc:UUID,
                             limit:int=20,after_document_id:UUID|None=None):
    """Manager-only active branch queue, excluding self-drafted or accepted returns.

    Advisory read; the approval POST repeats independent actor, receipt
    valuation, physical stock and idempotency checks under DB locks.
    """
    if type(token) is not str or not token:
        raise AutoStockDenied("FORBIDDEN")
    if (type(org) is not UUID or type(loc) is not UUID or
        type(limit) is not int or not 1<=limit<=50 or
        (after_document_id is not None and type(after_document_id) is not UUID)):
        raise AutoStockDenied("INVALID_INPUT")
    with conn.transaction():
        if not organization_role_allows(conn,token,org,"MANAGER",loc,
                                        for_mutation=True):
            raise AutoStockDenied("FORBIDDEN")
        actor=session_subject(conn,token,for_mutation=True)
        if actor is None:
            raise AutoStockDenied("FORBIDDEN")
        rows=conn.execute("""
            SELECT d.id,d.source_receipt_id,d.lot_id,s.article,l.batch_code,
                   b.code,d.quantity,d.reason,d.supplier_reference
            FROM auto_stock_supplier_return_document d
            JOIN auto_stock_lot l ON l.id=d.lot_id
              AND l.organization_id=d.organization_id
              AND l.location_id=d.location_id
            JOIN auto_stock_bin b ON b.id=l.bin_id
            JOIN auto_stock_sku s ON s.id=d.sku_id
              AND s.organization_id=d.organization_id
              AND s.location_id=d.location_id
            JOIN auto_organization o ON o.id=d.organization_id
              AND o.disabled_at IS NULL
            JOIN auto_service_location location ON location.id=d.location_id
              AND location.organization_id=d.organization_id
              AND location.disabled_at IS NULL
            JOIN auto_stock_receipt_document receipt
              ON receipt.id=d.source_receipt_id AND receipt.lot_id=d.lot_id
              AND receipt.organization_id=d.organization_id
              AND receipt.location_id=d.location_id
            JOIN auto_stock_receipt_acceptance accepted
              ON accepted.document_id=receipt.id
            JOIN auto_stock_receipt_valuation value
              ON value.document_id=receipt.id
            WHERE d.organization_id=%s AND d.location_id=%s
              AND d.created_by<>%s
              AND NOT EXISTS (
                SELECT 1 FROM auto_stock_supplier_return_acceptance approval
                WHERE approval.document_id=d.id
              )
              AND (%s::uuid IS NULL OR d.id>%s::uuid)
            ORDER BY d.id LIMIT %s
        """,(org,loc,actor,after_document_id,after_document_id,limit+1)).fetchall()
        next_id=rows[limit-1][0] if len(rows)>limit else None
        return ([{
            "documentId":str(document_id),"sourceReceiptId":str(receipt_id),
            "lotId":str(lot_id),"article":article,"batchCode":batch_code,
            "binCode":bin_code,"quantity":quantity,"reason":reason,
            "supplierReference":reference,
        } for document_id,receipt_id,lot_id,article,batch_code,bin_code,
              quantity,reason,reference in rows[:limit]],next_id)
