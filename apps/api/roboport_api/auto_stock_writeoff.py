"""Списание склада: WAREHOUSE создаёт акт, другой MANAGER принимает решение."""
from uuid import UUID,uuid4
import hashlib
import psycopg
from .auto_org_authority import organization_role_allows
from .auto_purchase_document_policy import require_full_stock_history
from .session_lookup import session_subject
from .keyset_cursor import encode_cursor,decode_cursor
from .auto_stock_service import AutoStockDenied,AutoStockConflict
from .auto_stock_serial_disposition import normalize_serials

REASONS=("DAMAGED","EXPIRED","UNUSABLE","OTHER")
ACTIONS=("APPROVE","REJECT","CANCEL")
SQL_FAILURES=(psycopg.errors.RaiseException,psycopg.errors.UniqueViolation,
              psycopg.errors.CheckViolation,psycopg.errors.ForeignKeyViolation,
              psycopg.errors.DeadlockDetected)


def _authority(conn,token,org,loc,role=None,capability=None):
    warehouse=organization_role_allows(conn,token,org,"WAREHOUSE",loc,for_mutation=True)
    manager=organization_role_allows(conn,token,org,"MANAGER",loc,for_mutation=True)
    actor=session_subject(conn,token,for_mutation=True)
    if actor is None or not (warehouse or manager) or (role=="WAREHOUSE" and not warehouse) or (role=="MANAGER" and not manager):
        raise AutoStockDenied("FORBIDDEN")
    require_full_stock_history(conn,token,org,loc,*((capability,) if capability else ()))
    return actor,warehouse,manager


def _permitted(conn,token,org,loc,capability):
    try:
        require_full_stock_history(conn,token,org,loc,capability)
        return True
    except AutoStockDenied:
        return False


def _note(note):
    if type(note) is not str or not 3<=len(note)<=240 or note!=note.strip() or any(ord(c)<32 for c in note):
        raise AutoStockDenied("INVALID_INPUT")


def _lock_key(conn,key):
    conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",(f"writeoff:{key}",))


def workspace(conn,token,org,loc,query="",status="ALL",cursor=None,lot_cursor=None):
    if type(query) is not str or len(query)>80 or any(ord(c)<32 for c in query) or status not in ("ALL","PENDING","APPROVED","REJECTED","CANCELED"):
        raise AutoStockDenied("INVALID_INPUT")
    query=query.strip()
    with conn.transaction():
        actor,warehouse,manager=_authority(conn,token,org,loc)
        can_draft=warehouse and _permitted(conn,token,org,loc,"CREATE")
        can_close=_permitted(conn,token,org,loc,"CLOSE")
        doc_scope=hashlib.sha256(f"writeoff-doc:{org}:{loc}:{status}".encode()).hexdigest()[:32]
        lot_scope=hashlib.sha256(f"writeoff-lot:{org}:{loc}:{query}".encode()).hexdigest()[:32]
        def position(value,scope):
            try:
                row=decode_cursor(value,scope,token) if value is not None else None
            except ValueError:
                raise AutoStockDenied("INVALID_INPUT") from None
            return (row[0],row[0],row[1]) if row else (None,None,None)
        lot_pos=position(lot_cursor,lot_scope)
        doc_pos=position(cursor,doc_scope)
        lots=conn.execute("""
          SELECT l.id,s.article,l.batch_code,b.code,lb.allocated,sb.on_hand,sb.reserved,l.created_at
          FROM auto_stock_lot l JOIN auto_stock_sku s ON s.id=l.sku_id
          JOIN auto_stock_bin b ON b.id=l.bin_id
          JOIN auto_stock_lot_balance lb ON lb.lot_id=l.id
          JOIN auto_stock_balance sb ON sb.sku_id=s.id
          WHERE l.organization_id=%s AND l.location_id=%s
            AND (%s='' OR strpos(lower(s.article),lower(%s))>0 OR strpos(lower(l.batch_code),lower(%s))>0)
            AND (%s::timestamptz IS NULL OR (l.created_at,l.id)>(%s::timestamptz,%s::uuid))
          ORDER BY l.created_at,l.id LIMIT 51
        """,(org,loc,query,query,query,*lot_pos)).fetchall() if can_draft else []
        rows=conn.execute("""
          SELECT d.id,d.lot_id,s.article,l.batch_code,b.code,d.quantity,d.reason_code,d.note,
                 d.created_by,d.created_at,x.action,x.id,x.note,x.actor_id,x.created_at,
                 p.id,p.stock_event_id,p.lot_debit_id,d.serial_ids
          FROM auto_stock_writeoff_document d
          JOIN auto_stock_lot l ON l.id=d.lot_id JOIN auto_stock_sku s ON s.id=l.sku_id
          JOIN auto_stock_bin b ON b.id=l.bin_id
          LEFT JOIN auto_stock_writeoff_decision x ON x.document_id=d.id
          LEFT JOIN auto_stock_lot_disposition p ON p.id=x.disposition_id
          WHERE d.organization_id=%s AND d.location_id=%s
            AND (%s='ALL' OR %s=CASE x.action WHEN 'APPROVE' THEN 'APPROVED'
                WHEN 'REJECT' THEN 'REJECTED' WHEN 'CANCEL' THEN 'CANCELED' ELSE 'PENDING' END)
            AND (%s::timestamptz IS NULL OR (d.created_at,d.id)<(%s::timestamptz,%s::uuid))
          ORDER BY d.created_at DESC,d.id DESC LIMIT 51
        """,(org,loc,status,status,*doc_pos)).fetchall()
        items=[]
        for ident,lot,article,batch,bin_code,qty,reason,note,author,created,action,decision,decision_note,reviewer,decided,disposition,event,debit,serials in rows[:50]:
            items.append({"documentId":str(ident),"lotId":str(lot),"article":article,"batchCode":batch,
                "binCode":bin_code,"quantity":str(qty),"reasonCode":reason,"note":note,
                "createdBy":str(author),"createdAt":created.isoformat(),"serialIds":[str(i) for i in serials],
                "status":{None:"PENDING","APPROVE":"APPROVED","REJECT":"REJECTED","CANCEL":"CANCELED"}[action],
                "canReview":action is None and manager and can_close and author!=actor,
                "canCancel":action is None and warehouse and can_close and author==actor,
                "decision":None if decision is None else {"id":str(decision),"note":decision_note,
                    "actorId":str(reviewer),"createdAt":decided.isoformat(),
                    "dispositionId":str(disposition) if disposition else None,
                    "stockEventId":str(event) if event else None,"lotDebitId":str(debit) if debit else None}})
        return {"syntheticOnly":True,"organizationId":str(org),"locationId":str(loc),
                "canDraft":can_draft,"items":items,
                "nextCursor":encode_cursor(rows[49][9],rows[49][0],doc_scope,token) if len(rows)>50 else None,
                "lots":[{"lotId":str(i),"article":a,"batchCode":batch,"binCode":b,
                    "available":str(max(0,min(allocated,on_hand-reserved)))}
                    for i,a,batch,b,allocated,on_hand,reserved,_ in lots[:50]],
                "nextLotCursor":encode_cursor(lots[49][7],lots[49][0],lot_scope,token) if len(lots)>50 else None}


def draft(conn,token,org,loc,lot,key,quantity,reason,note,*,serial_ids=()):
    _note(note)
    if type(quantity) is not int or not 1<=quantity<=1000000000 or reason not in REASONS:
        raise AutoStockDenied("INVALID_INPUT")
    serials=normalize_serials(serial_ids,quantity)
    with conn.transaction():
        actor,_,_=_authority(conn,token,org,loc,"WAREHOUSE","CREATE")
        _lock_key(conn,key)
        prior=conn.execute("""
          SELECT id,organization_id,location_id,lot_id,quantity,reason_code,note,created_by,serial_ids
          FROM auto_stock_writeoff_document WHERE request_id=%s
        """,(key,)).fetchone()
        if prior:
            if prior[1:]!=(org,loc,lot,quantity,reason,note,actor,serials):
                raise AutoStockConflict("CONFLICT")
            return {"documentId":str(prior[0]),"replayed":True,"syntheticOnly":True}
        if not conn.execute("""
          SELECT id FROM auto_stock_lot WHERE id=%s AND organization_id=%s AND location_id=%s FOR SHARE
        """,(lot,org,loc)).fetchone():
            raise AutoStockDenied("FORBIDDEN")
        ident=uuid4()
        conn.execute("""
          INSERT INTO auto_stock_writeoff_document
            (id,request_id,organization_id,location_id,lot_id,quantity,reason_code,note,created_by,serial_ids)
          VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """,(ident,key,org,loc,lot,quantity,reason,note,actor,serials))
        return {"documentId":str(ident),"replayed":False,"syntheticOnly":True}


def decide(conn,token,org,loc,document,key,action,note):
    _note(note)
    if action not in ACTIONS:
        raise AutoStockDenied("INVALID_INPUT")
    with conn.transaction():
        actor,_,_=_authority(conn,token,org,loc,"WAREHOUSE" if action=="CANCEL" else "MANAGER","CLOSE")
        _lock_key(conn,key)
        doc=conn.execute("""
          SELECT lot_id,quantity,note,created_by FROM auto_stock_writeoff_document
          WHERE id=%s AND organization_id=%s AND location_id=%s FOR UPDATE
        """,(document,org,loc)).fetchone()
        if doc is None or (action=="CANCEL")!=(actor==doc[3]):
            raise AutoStockDenied("FORBIDDEN")
        prior=conn.execute("""
          SELECT id,document_id,action,note,actor_id,disposition_id
          FROM auto_stock_writeoff_decision WHERE request_id=%s
        """,(key,)).fetchone()
        if prior:
            if prior[1:5]!=(document,action,note,actor):
                raise AutoStockConflict("CONFLICT")
            return {"decisionId":str(prior[0]),"replayed":True,"syntheticOnly":True}
        if conn.execute("SELECT 1 FROM auto_stock_writeoff_decision WHERE document_id=%s",(document,)).fetchone():
            raise AutoStockConflict("CONFLICT")
        ident=uuid4()
        try:
            with conn.transaction():
                disposition=None
                if action=="APPROVE":
                    # Блокируем тот же SKU до проверки ключа, что и внутренний ledger.
                    conn.execute("SELECT s.id FROM auto_stock_sku s JOIN auto_stock_lot l ON l.sku_id=s.id WHERE l.id=%s FOR UPDATE OF s",(doc[0],))
                    # Не присваиваем ранее совершённое внутреннее списание.
                    if conn.execute("SELECT 1 FROM auto_stock_lot_disposition WHERE request_id=%s",(key,)).fetchone():
                        raise AutoStockConflict("CONFLICT")
                    disposition=conn.execute("""
                      SELECT record_auto_stock_lot_disposition(%s,%s,'WRITE_OFF',%s,%s,NULL)
                    """,(doc[0],key,doc[1],doc[2])).fetchone()[0]
                conn.execute("""
                  INSERT INTO auto_stock_writeoff_decision
                    (id,request_id,document_id,action,note,actor_id,disposition_id)
                  VALUES(%s,%s,%s,%s,%s,%s,%s)
                """,(ident,key,document,action,note,actor,disposition))
                if action=="APPROVE":
                    # Терминальные серии связаны trigger'ом решения до проверки
                    # физического остатка; любой конфликт откатывает весь акт.
                    conn.execute("SET CONSTRAINTS auto_stock_lot_disposition_pair,auto_stock_serial_allocation_guard IMMEDIATE")
                    conn.execute("SET CONSTRAINTS auto_stock_lot_disposition_pair,auto_stock_serial_allocation_guard DEFERRED")
        except SQL_FAILURES:
            raise AutoStockConflict("CONFLICT") from None
        return {"decisionId":str(ident),"replayed":False,"syntheticOnly":True}
