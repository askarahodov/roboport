"""PostgreSQL-atomic stock reservations with live ACL and compatibility."""
import hashlib
import json
from uuid import UUID, uuid4
from .session_lookup import session_subject


class StockConflict(Exception):
    pass


class StockForbidden(Exception):
    pass


def _staff(conn, token, ticket):
    actor=session_subject(conn,token)
    if actor is None:
        raise StockForbidden()
    permitted=conn.execute("""SELECT EXISTS(
      SELECT 1 FROM user_role r JOIN acl_entry a
        ON a.user_id=r.user_id AND a.ticket_id=%s AND a.action='stock.reserve'
       AND a.revoked_at IS NULL AND (a.expires_at IS NULL OR a.expires_at>now())
      WHERE r.user_id=%s AND r.role_code='STOCK'
       AND r.revoked_at IS NULL AND (r.expires_at IS NULL OR r.expires_at>now())
    )""",(ticket,actor)).fetchone()[0]
    if not permitted:
        raise StockForbidden()
    return actor


def _receipt(conn, actor, key, intent, execute):
    if (not isinstance(key,str) or not 1<=len(key)<=128
            or not key.isascii() or not key.isprintable()):
        raise ValueError("invalid idempotency key")
    # Account lock serializes two different targets with the same key.
    conn.execute("SELECT id FROM app_user WHERE id=%s FOR UPDATE",(actor,))
    fingerprint=hashlib.sha256(json.dumps(
        intent,sort_keys=True,separators=(",",":")).encode()).hexdigest()
    old=conn.execute("""SELECT fingerprint,response FROM inventory_receipt
                         WHERE actor_id=%s AND request_key=%s""",(actor,key)).fetchone()
    if old:
        if old[0]!=fingerprint:
            raise StockConflict("idempotency conflict")
        return old[1]
    result=execute()
    conn.execute("""INSERT INTO inventory_receipt(actor_id,request_key,fingerprint,response)
                    VALUES (%s,%s,%s,%s::jsonb)""",
                 (actor,key,fingerprint,json.dumps(result)))
    return result


def reserve(conn, token, ticket:UUID, lot:UUID, qty:int, key:str):
    if (not isinstance(ticket,UUID) or not isinstance(lot,UUID)
            or type(qty) is not int or qty<=0):
        raise ValueError("invalid reserve")
    actor=_staff(conn,token,ticket)
    def effect():
        identity=conn.execute("""SELECT d.model_variant,d.region,d.revision,t.state
          FROM service_ticket t LEFT JOIN robot_device d ON d.id=t.device_id
          WHERE t.id=%s FOR UPDATE OF t""",(ticket,)).fetchone()
        if identity is None or any(x is None for x in identity[:3]) or identity[3] not in (
                'APPROVED','AWAITING_PARTS','REPAIRING'):
            raise StockConflict("ticket/device not eligible")
        row=conn.execute("SELECT part_number,available_qty,reserved_qty FROM stock_lot WHERE id=%s FOR UPDATE",
                         (lot,)).fetchone()
        if row is None:
            raise StockConflict("lot unavailable")
        part,available,reserved=row
        compatible=conn.execute("""SELECT id FROM part_compatibility
          WHERE part_number=%s AND model_variant=%s AND region=%s AND revision=%s
            AND verified=true AND revoked_at IS NULL""",
            (part,*identity[:3])).fetchone()
        if compatible is None:
            raise StockConflict("unverified compatibility")
        if available-reserved<qty:
            raise StockConflict("insufficient inventory")
        reservation=uuid4()
        conn.execute("""UPDATE stock_lot SET reserved_qty=reserved_qty+%s WHERE id=%s""",(qty,lot))
        conn.execute("""INSERT INTO stock_reservation
          (id,lot_id,ticket_id,compatibility_id,actor_id,qty,status)
          VALUES (%s,%s,%s,%s,%s,%s,'ACTIVE')""",
          (reservation,lot,ticket,compatible[0],actor,qty))
        conn.execute("""INSERT INTO stock_movement
          (id,reservation_id,lot_id,delta_reserved,action)
          VALUES (%s,%s,%s,%s,'RESERVE')""",(uuid4(),reservation,lot,qty))
        conn.execute("INSERT INTO audit_event(id,ticket_id,action) VALUES (%s,%s,'stock.reserve')",
                     (uuid4(),ticket))
        return {"id":str(reservation),"status":"ACTIVE","quantity":qty}
    return _receipt(conn,actor,key,{"operation":"reserve","ticket":str(ticket),
                                    "lot":str(lot),"quantity":qty},effect)


def cancel(conn, token, reservation:UUID, key:str):
    if not isinstance(reservation,UUID):
        raise ValueError("invalid reservation")
    ref=conn.execute("SELECT ticket_id FROM stock_reservation WHERE id=%s",(reservation,)).fetchone()
    if ref is None:
        raise StockForbidden()
    ticket=ref[0]
    actor=_staff(conn,token,ticket)
    def effect():
        # Lock lot first to enforce consistent order with reserve.
        row=conn.execute("SELECT lot_id FROM stock_reservation WHERE id=%s",(reservation,)).fetchone()
        if row is None:
            raise StockForbidden()
        lot=row[0]
        conn.execute("SELECT id FROM stock_lot WHERE id=%s FOR UPDATE",(lot,))
        item=conn.execute("""SELECT qty,status FROM stock_reservation WHERE id=%s FOR UPDATE""",
                          (reservation,)).fetchone()
        if item[1]=='CANCELLED':
            return {"id":str(reservation),"status":"CANCELLED"}
        conn.execute("UPDATE stock_lot SET reserved_qty=reserved_qty-%s WHERE id=%s",(item[0],lot))
        conn.execute("""UPDATE stock_reservation SET status='CANCELLED',cancelled_at=now()
                     WHERE id=%s""",(reservation,))
        conn.execute("""INSERT INTO stock_movement
          (id,reservation_id,lot_id,delta_reserved,action)
          VALUES (%s,%s,%s,%s,'RELEASE')""",(uuid4(),reservation,lot,-item[0]))
        conn.execute("INSERT INTO audit_event(id,ticket_id,action) VALUES (%s,%s,'stock.cancel')",
                     (uuid4(),ticket))
        return {"id":str(reservation),"status":"CANCELLED"}
    return _receipt(conn,actor,key,{"operation":"cancel","reservation":str(reservation)},effect)
