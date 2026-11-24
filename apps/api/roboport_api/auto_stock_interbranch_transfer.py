"""Internal dual-branch-authorized physical inventory transfer."""
from uuid import UUID
import psycopg
from .auto_org_authority import organization_role_allows
from .session_lookup import session_subject
from .auto_stock_lot_transfer import transfer_stock_lot
from .keyset_cursor import encode_cursor,decode_cursor
import hashlib
from .auto_stock_service import AutoStockDenied, AutoStockConflict


def transfer_interbranch_stock(conn: psycopg.Connection, token: str,
    organization_id: UUID, source_location_id: UUID,
    destination_location_id: UUID, source_lot_id: UUID,
    destination_lot_id: UUID, request_id: UUID, quantity: int) -> UUID:
    if (type(token) is not str or not token
        or not all(isinstance(x,UUID) for x in
                   (organization_id,source_location_id,destination_location_id,
                    source_lot_id,destination_lot_id,request_id))
        or source_location_id==destination_location_id
        or source_lot_id==destination_lot_id
        or type(quantity) is not int or not 1<=quantity<=1_000_000_000):
        raise AutoStockDenied("INVALID_INPUT")
    with conn.transaction():
        # Two distinct branch grants are mandatory, not merely one source grant.
        for location in (source_location_id,destination_location_id):
            if not organization_role_allows(conn,token,organization_id,
                   "WAREHOUSE",location,for_mutation=True):
                raise AutoStockDenied("FORBIDDEN")
        matches=conn.execute("""
            SELECT id FROM auto_stock_lot
            WHERE organization_id=%s AND
             ((id=%s AND location_id=%s) OR (id=%s AND location_id=%s))
            FOR SHARE
        """,(organization_id,source_lot_id,source_location_id,
             destination_lot_id,destination_location_id)).fetchall()
        if len(matches)!=2:
            raise AutoStockDenied("FORBIDDEN")
        try:
            with conn.transaction():
                result=conn.execute(
                    "SELECT record_auto_stock_interbranch_transfer(%s,%s,%s,%s)",
                    (source_lot_id,destination_lot_id,request_id,quantity)
                ).fetchone()[0]
                conn.execute("SET CONSTRAINTS auto_stock_serial_allocation_guard IMMEDIATE")
                conn.execute("SET CONSTRAINTS auto_stock_serial_allocation_guard DEFERRED")
                return result
        except (psycopg.errors.RaiseException,psycopg.errors.UniqueViolation,
                psycopg.errors.CheckViolation):
            raise AutoStockConflict("CONFLICT") from None


def _pair_actor(conn,token,org,src,dst,mode='INTERBRANCH'):
    if mode not in ('INTERBRANCH','LOCAL') or (src==dst)!=(mode=='LOCAL'):
        raise AutoStockDenied("INVALID_INPUT")
    for loc in sorted({src,dst},key=str):
        if not organization_role_allows(conn,token,org,"WAREHOUSE",loc,for_mutation=True):
            raise AutoStockDenied("FORBIDDEN")
    actor=session_subject(conn,token,for_mutation=True)
    if actor is None:
        raise AutoStockDenied("FORBIDDEN")
    return actor


def transfer_workspace(conn,token,org,src,dst,query="",cursor=None,
                       source_cursor=None,destination_cursor=None,mode="INTERBRANCH"):
    if type(query) is not str or len(query)>80 or any(ord(c)<32 for c in query):
        raise AutoStockDenied("INVALID_INPUT")
    query=query.strip()
    with conn.transaction():
        _pair_actor(conn,token,org,src,dst,mode)
        scope=hashlib.sha256(f"transfer:{mode}:{org}:{src}:{dst}".encode()).hexdigest()[:32]

        def position(value,scope):
            try:
                after=decode_cursor(value,scope,token) if value is not None else None
            except ValueError:
                raise AutoStockDenied("INVALID_INPUT") from None
            return (after[0],after[0],after[1]) if after else (None,None,None)

        options=[]
        for side,loc,value in (("source",src,source_cursor),("destination",dst,destination_cursor)):
            lot_scope=hashlib.sha256(f"{scope}:{side}:{query}".encode()).hexdigest()[:32]
            rows=conn.execute("""
              SELECT l.id,s.article,l.batch_code,b.code,lb.allocated,sb.on_hand,sb.reserved,l.created_at
              FROM auto_stock_lot l JOIN auto_stock_sku s ON s.id=l.sku_id
              JOIN auto_stock_bin b ON b.id=l.bin_id
              JOIN auto_stock_lot_balance lb ON lb.lot_id=l.id
              JOIN auto_stock_balance sb ON sb.sku_id=s.id
              WHERE l.organization_id=%s AND l.location_id=%s
                AND (strpos(lower(s.article),lower(%s))>0 OR %s='')
                AND (%s::timestamptz IS NULL OR (l.created_at,l.id)>(%s::timestamptz,%s::uuid))
              ORDER BY l.created_at,l.id LIMIT 51
            """,(org,loc,query,query,*position(value,lot_scope))).fetchall()
            options.append({"truncated":len(rows)>50,
                "nextCursor":encode_cursor(rows[49][7],rows[49][0],lot_scope,token) if len(rows)>50 else None,
                "items":[{"lotId":str(i),"article":a,"batchCode":batch,"binCode":bin_code,
                 "allocated":str(allocated),"available":str(allocated if mode=="LOCAL" else
                     max(0,min(allocated,on_hand-reserved)))}
                 for i,a,batch,bin_code,allocated,on_hand,reserved,_ in rows[:50]]})
        if mode=="LOCAL":
            rows=conn.execute("""
              SELECT t.id,t.created_at,t.source_lot_id,t.destination_lot_id,s.article,
                     t.quantity,r.actor_id,t.debit_event_id,t.credit_event_id
              FROM auto_stock_lot_transfer t
              JOIN auto_stock_sku s ON s.id=t.sku_id
              JOIN auto_stock_lot a ON a.id=t.source_lot_id
              JOIN auto_stock_lot b ON b.id=t.destination_lot_id
              LEFT JOIN auto_stock_local_transfer_receipt r ON r.transfer_id=t.id
              WHERE s.organization_id=%s AND a.location_id=%s AND b.location_id=%s
                AND (%s::timestamptz IS NULL OR (t.created_at,t.id)<(%s::timestamptz,%s::uuid))
              ORDER BY t.created_at DESC,t.id DESC LIMIT 51
            """,(org,src,dst,*position(cursor,scope))).fetchall()
        else:
            rows=conn.execute("""
              SELECT t.id,t.created_at,t.source_lot_id,t.destination_lot_id,s.article,
                     t.quantity,r.actor_id,t.debit_stock_id,t.credit_stock_id
              FROM auto_stock_interbranch_transfer t
              JOIN auto_stock_lot a ON a.id=t.source_lot_id
              JOIN auto_stock_lot b ON b.id=t.destination_lot_id
              JOIN auto_stock_sku s ON s.id=t.source_sku_id
              LEFT JOIN auto_stock_transfer_receipt r ON r.transfer_id=t.id
              WHERE t.organization_id=%s AND a.location_id=%s AND b.location_id=%s
                AND (%s::timestamptz IS NULL OR (t.created_at,t.id)<(%s::timestamptz,%s::uuid))
              ORDER BY t.created_at DESC,t.id DESC LIMIT 51
            """,(org,src,dst,*position(cursor,scope))).fetchall()
        return {"syntheticOnly":True,"mode":mode,"source":options[0],"destination":options[1],
                "items":[{"transferId":str(i),"createdAt":stamp.isoformat(),
                    "sourceLotId":str(a),"destinationLotId":str(b),"article":article,
                    "quantity":str(q),"actorId":str(actor) if actor else None,
                    **({"debitAllocationId":str(debit),"creditAllocationId":str(credit)} if mode=="LOCAL"
                       else {"debitStockId":str(debit),"creditStockId":str(credit)})}
                    for i,stamp,a,b,article,q,actor,debit,credit in rows[:50]],
                "nextCursor":encode_cursor(rows[49][1],rows[49][0],scope,token) if len(rows)>50 else None}


def confirm_interbranch_transfer(conn,token,org,src,dst,source,destination,key,quantity):
    with conn.transaction():
        actor=_pair_actor(conn,token,org,src,dst)
        # Сериализуем один ключ до поиска receipt; старый внутренний журнал
        # без автора не может быть присвоен новым HTTP-запросом.
        conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",(f"transfer:{key}",))
        prior=conn.execute("""
          SELECT t.id,r.actor_id,t.organization_id,t.source_lot_id,t.destination_lot_id,t.quantity,
                 a.location_id,b.location_id
          FROM auto_stock_interbranch_transfer t
          LEFT JOIN auto_stock_transfer_receipt r ON r.transfer_id=t.id
          JOIN auto_stock_lot a ON a.id=t.source_lot_id
          JOIN auto_stock_lot b ON b.id=t.destination_lot_id
          WHERE t.request_id=%s
        """,(key,)).fetchone()
        if prior:
            if prior[1:]!=(actor,org,source,destination,quantity,src,dst):
                raise AutoStockConflict("CONFLICT")
            return {"syntheticOnly":True,"transferId":str(prior[0]),"replayed":True}
        ident=transfer_interbranch_stock(conn,token,org,src,dst,source,destination,key,quantity)
        conn.execute("INSERT INTO auto_stock_transfer_receipt(transfer_id,actor_id) VALUES(%s,%s)",
                     (ident,actor))
        return {"syntheticOnly":True,"transferId":str(ident),"replayed":False}


def confirm_local_transfer(conn,token,org,loc,source,destination,key,quantity):
    with conn.transaction():
        actor=_pair_actor(conn,token,org,loc,loc,"LOCAL")
        conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",(f"local-transfer:{key}",))
        prior=conn.execute("""
          SELECT t.id,r.actor_id,s.organization_id,s.location_id,t.source_lot_id,
                 t.destination_lot_id,t.quantity
          FROM auto_stock_lot_transfer t
          JOIN auto_stock_sku s ON s.id=t.sku_id
          LEFT JOIN auto_stock_local_transfer_receipt r ON r.transfer_id=t.id
          WHERE t.request_id=%s
        """,(key,)).fetchone()
        if prior:
            if prior[1:]!=(actor,org,loc,source,destination,quantity):
                raise AutoStockConflict("CONFLICT")
            return {"syntheticOnly":True,"transferId":str(prior[0]),"replayed":True}
        ident=transfer_stock_lot(conn,token,org,loc,source,destination,key,quantity)
        conn.execute("INSERT INTO auto_stock_local_transfer_receipt(transfer_id,actor_id) VALUES(%s,%s)",
                     (ident,actor))
        return {"syntheticOnly":True,"transferId":str(ident),"replayed":False}
