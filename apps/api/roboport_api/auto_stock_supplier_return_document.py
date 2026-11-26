"""Accepted-receipt supplier returns with independently authorized warehouse/manager steps.

Internal synthetic-only: purchase cost/VAT attribution is not a legal credit note.
"""
from uuid import UUID, uuid4
import psycopg
from .auto_org_authority import organization_role_allows
from .session_lookup import session_subject
from .auto_stock_service import AutoStockConflict, AutoStockDenied
from .auto_stock_serial_disposition import normalize_serials
from .auto_purchase_document_policy import require_purchase_document,receipt_history_date

SQL_FAILURES=(psycopg.errors.RaiseException,psycopg.errors.UniqueViolation,
              psycopg.errors.CheckViolation,psycopg.errors.ForeignKeyViolation)


def _validate(token,*ids):
    if type(token) is not str or not token or any(type(i) is not UUID for i in ids):
        raise AutoStockDenied("INVALID_INPUT")


def _run(conn,token,org,loc,role,sql,args,*,cap,created_at):
    with conn.transaction():
        if not organization_role_allows(conn,token,org,role,loc,for_mutation=True):
            raise AutoStockDenied("FORBIDDEN")
        require_purchase_document(conn,token,org,loc,cap,created_at=created_at)
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
    quantity:int,reason:str,supplier_reference:str,*,serial_ids=())->UUID:
    _validate(token,org,loc,receipt_id,request_id)
    if (type(quantity) is not int or not 1<=quantity<=1_000_000_000
      or type(reason) is not str or not 3<=len(reason)<=240
      or reason!=reason.strip()
      or type(supplier_reference) is not str
      or not 3<=len(supplier_reference)<=160
      or supplier_reference!=supplier_reference.strip()):
        raise AutoStockDenied("INVALID_INPUT")
    serials=normalize_serials(serial_ids,quantity)
    with conn.transaction():
        if not organization_role_allows(conn,token,org,"WAREHOUSE",loc,for_mutation=True):
            raise AutoStockDenied("FORBIDDEN")
        row=conn.execute("""SELECT id,sku_id,quantity,lot_id FROM auto_stock_receipt_document
            WHERE id=%s AND organization_id=%s AND location_id=%s FOR SHARE""",
            (receipt_id,org,loc)).fetchone()
        if row is None: raise AutoStockDenied("FORBIDDEN")
        history=receipt_history_date(conn,org,loc,receipt_id)
        require_purchase_document(conn,token,org,loc,"CREATE",created_at=history)
        # Match the existing approval/ledger serialization key. A new draft
        # cannot claim quantity already returned against this receipt, even
        # when another receipt replenished the same physical lot. Preserve
        # exact replay only for the same actor and complete canonical payload.
        conn.execute("SELECT id FROM auto_stock_sku WHERE id=%s FOR UPDATE",(row[1],))
        actor=session_subject(conn,token,for_mutation=True)
        if actor is None: raise AutoStockDenied("FORBIDDEN")
        prior=conn.execute("""SELECT id,source_receipt_id,organization_id,location_id,
              quantity,reason,supplier_reference,created_by,serial_ids
            FROM auto_stock_supplier_return_document WHERE request_id=%s""",(request_id,)).fetchone()
        if prior is not None:
            if prior[1:]!=(receipt_id,org,loc,quantity,reason,supplier_reference,actor,serials):
                raise AutoStockConflict("CONFLICT")
            return prior[0]
        returned=conn.execute("""SELECT COALESCE(sum(d.quantity),0)
            FROM auto_stock_supplier_return_document d
            JOIN auto_stock_supplier_return_acceptance a ON a.document_id=d.id
            WHERE d.source_receipt_id=%s""",(receipt_id,)).fetchone()[0]
        if returned+quantity>row[2]:
            raise AutoStockConflict("CONFLICT")
        ident=uuid4()
        # Новая колонка включается непосредственно в неизменяемый акт.
        # Исходный trigger проверяет accepted/valued receipt и устанавливает actor.
        try:
            with conn.transaction():
                conn.execute("SELECT set_config('roboport.supplier_return_actor',%s,true)",(str(actor),))
                conn.execute("""
                  INSERT INTO auto_stock_supplier_return_document
                    (id,request_id,organization_id,location_id,lot_id,sku_id,
                     source_receipt_id,quantity,reason,supplier_reference,created_by,serial_ids)
                  VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,(ident,request_id,org,loc,row[3],row[1],receipt_id,quantity,reason,
                     supplier_reference,actor,serials))
        except SQL_FAILURES:
            raise AutoStockConflict("CONFLICT") from None
        finally:
            conn.execute("SELECT set_config('roboport.supplier_return_actor','',true)")
        return ident


def approve_supplier_return(conn:psycopg.Connection,token:str,
    org:UUID,loc:UUID,document_id:UUID,request_id:UUID)->UUID:
    _validate(token,org,loc,document_id,request_id)
    with conn.transaction():
        if not organization_role_allows(conn,token,org,"MANAGER",loc,for_mutation=True):
            raise AutoStockDenied("FORBIDDEN")
        row=conn.execute("""SELECT d.id,least(d.created_at,r.created_at),r.id,d.sku_id
            FROM auto_stock_supplier_return_document d
            JOIN auto_stock_receipt_document r ON r.id=d.source_receipt_id
              AND r.organization_id=d.organization_id AND r.location_id=d.location_id
            WHERE d.id=%s AND d.organization_id=%s AND d.location_id=%s FOR SHARE OF d,r""",
            (document_id,org,loc)).fetchone()
        if row is None: raise AutoStockDenied("FORBIDDEN")
        history=min(row[1],receipt_history_date(conn,org,loc,row[2]))
        require_purchase_document(conn,token,org,loc,"CLOSE",created_at=history)
        conn.execute("SELECT id FROM auto_stock_sku WHERE id=%s FOR UPDATE",(row[3],))
        if conn.execute("""SELECT 1 FROM auto_stock_supplier_return_terminal
                           WHERE document_id=%s AND action<>'APPROVE'""",(document_id,)).fetchone():
            raise AutoStockConflict("CONFLICT")
        return _run(conn,token,org,loc,"MANAGER",
            "SELECT accept_auto_stock_supplier_return_document(%s,%s)",
            (document_id,request_id),cap="CLOSE",
            created_at=history)


def decide_supplier_return(conn:psycopg.Connection,token:str,
    org:UUID,loc:UUID,document_id:UUID,request_id:UUID,action:str,note:str)->UUID:
    """Автор отменяет черновик, независимый MANAGER отклоняет; проводок нет."""
    _validate(token,org,loc,document_id,request_id)
    if (action not in ("CANCEL","REJECT") or type(note) is not str
        or not 3<=len(note)<=240 or note!=note.strip()
        or any(ord(c)<32 for c in note)):
        raise AutoStockDenied("INVALID_INPUT")
    role="WAREHOUSE" if action=="CANCEL" else "MANAGER"
    with conn.transaction():
        if not organization_role_allows(conn,token,org,role,loc,for_mutation=True):
            raise AutoStockDenied("FORBIDDEN")
        row=conn.execute("""SELECT d.created_by,d.sku_id,least(d.created_at,r.created_at),r.id
            FROM auto_stock_supplier_return_document d
            JOIN auto_stock_receipt_document r ON r.id=d.source_receipt_id
              AND r.organization_id=d.organization_id AND r.location_id=d.location_id
            WHERE d.id=%s AND d.organization_id=%s AND d.location_id=%s FOR SHARE OF d,r""",
            (document_id,org,loc)).fetchone()
        if row is None:
            raise AutoStockDenied("FORBIDDEN")
        history=min(row[2],receipt_history_date(conn,org,loc,row[3]))
        require_purchase_document(conn,token,org,loc,"CLOSE",created_at=history)
        actor=session_subject(conn,token,for_mutation=True)
        if actor is None or (action=="CANCEL")!=(actor==row[0]):
            raise AutoStockDenied("FORBIDDEN")
        conn.execute("SELECT id FROM auto_stock_sku WHERE id=%s FOR UPDATE",(row[1],))
        prior=conn.execute("""SELECT id,document_id,action,note,actor_id
            FROM auto_stock_supplier_return_terminal WHERE request_id=%s""",(request_id,)).fetchone()
        if prior:
            if prior[1:]!=(document_id,action,note,actor):
                raise AutoStockConflict("CONFLICT")
            return prior[0]
        if conn.execute("""SELECT 1 FROM auto_stock_supplier_return_terminal
                           WHERE document_id=%s""",(document_id,)).fetchone():
            raise AutoStockConflict("CONFLICT")
        ident=uuid4()
        try:
            with conn.transaction():
                conn.execute("SELECT set_config('roboport.supplier_return_actor',%s,true)",(str(actor),))
                conn.execute("""INSERT INTO auto_stock_supplier_return_terminal
                    (id,request_id,document_id,action,note,actor_id)
                    VALUES(%s,%s,%s,%s,%s,%s)""",(ident,request_id,document_id,action,note,actor))
        except SQL_FAILURES:
            raise AutoStockConflict("CONFLICT") from None
        finally:
            conn.execute("SELECT set_config('roboport.supplier_return_actor','',true)")
        return ident



def pending_supplier_returns(conn:psycopg.Connection,token:str,org:UUID,loc:UUID,
                             limit:int=20,after_document_id:UUID|None=None,*,own:bool=False):
    """Открытые акты филиала: чужие для MANAGER, собственные для WAREHOUSE.

    Терминальные акты не попадают в очередь. Мутация повторяет все права.
    """
    if type(token) is not str or not token:
        raise AutoStockDenied("FORBIDDEN")
    if (type(org) is not UUID or type(loc) is not UUID or
        type(limit) is not int or not 1<=limit<=50 or type(own) is not bool or
        (after_document_id is not None and type(after_document_id) is not UUID)):
        raise AutoStockDenied("INVALID_INPUT")
    with conn.transaction():
        if not organization_role_allows(conn,token,org,"WAREHOUSE" if own else "MANAGER",loc,
                                        for_mutation=True):
            raise AutoStockDenied("FORBIDDEN")
        if require_purchase_document(conn,token,org,loc) is not None:
            raise AutoStockDenied("FORBIDDEN")
        actor=session_subject(conn,token,for_mutation=True)
        if actor is None:
            raise AutoStockDenied("FORBIDDEN")
        rows=conn.execute("""
            SELECT d.id,d.source_receipt_id,d.lot_id,s.article,l.batch_code,
                   b.code,d.quantity,d.reason,d.supplier_reference,d.serial_ids
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
              AND ((%s AND d.created_by=%s) OR (NOT %s AND d.created_by<>%s))
              AND NOT EXISTS (
                SELECT 1 FROM auto_stock_supplier_return_terminal terminal
                WHERE terminal.document_id=d.id
              )
              AND (%s::uuid IS NULL OR d.id>%s::uuid)
            ORDER BY d.id LIMIT %s
        """,(org,loc,own,actor,own,actor,after_document_id,after_document_id,limit+1)).fetchall()
        next_id=rows[limit-1][0] if len(rows)>limit else None
        return ([{
            "documentId":str(document_id),"sourceReceiptId":str(receipt_id),
            "lotId":str(lot_id),"article":article,"batchCode":batch_code,
            "binCode":bin_code,"quantity":quantity,"reason":reason,
            "supplierReference":reference,"serialIds":[str(i) for i in serials],
        } for document_id,receipt_id,lot_id,article,batch_code,bin_code,
              quantity,reason,reference,serials in rows[:limit]],next_id)
