"""Customer-owned automotive WorkOrders: scoped discovery, history and consent context.

No VIN/full customer identity, stock suppliers, legacy ticket data or production
endpoints. Unlike vehicle-level history, access is bound to the *exact* original
verified vehicle/customer link on the appointment. A new owner of the same car
cannot inherit the previous customer's order or documents.
"""
import hashlib
from uuid import UUID
import psycopg
from .session_lookup import session_subject
from .keyset_cursor import encode_cursor, decode_cursor
from .auto_work_order_estimate import _estimate_state
from .auto_work_order_execution import _state
from .auto_workshop_qc import _snapshot


class CustomerOrderDenied(ValueError):
    pass


class CustomerOrderInvalid(ValueError):
    pass


# One trusted, immutable customer/vehicle link per historical appointment.
# Current booking follows reschedules, never the original canceled booking.
_BASE = """
  FROM auto_work_order w
  JOIN auto_appointment a ON a.id=w.appointment_id
    AND a.organization_id=w.organization_id AND a.location_id=w.location_id
  JOIN auto_vehicle_customer_link proof ON proof.id=a.customer_link_id
    AND proof.vehicle_id=a.vehicle_id
    AND proof.verified_at IS NOT NULL AND proof.verified_by IS NOT NULL
    AND proof.revoked_at IS NULL
  JOIN auto_vehicle v ON v.id=a.vehicle_id
  JOIN auto_organization org ON org.id=w.organization_id
    AND org.disabled_at IS NULL
  JOIN auto_service_location loc ON loc.id=w.location_id
    AND loc.organization_id=org.id AND loc.disabled_at IS NULL
  JOIN LATERAL (
     SELECT COALESCE((
       SELECT r.new_booking_id FROM auto_appointment_reschedule r
       WHERE r.appointment_id=a.id ORDER BY r.created_at DESC,r.id DESC LIMIT 1
     ),a.booking_id) AS current_id
  ) current_booking ON TRUE
  JOIN auto_bay_booking b ON b.id=current_booking.current_id
    AND b.organization_id=w.organization_id AND b.location_id=w.location_id
  WHERE EXISTS (
      SELECT 1 FROM account_customer_link acl
      WHERE acl.user_id=%s AND acl.customer_id=proof.customer_id
        AND acl.revoked_at IS NULL
  )
    AND EXISTS (
      SELECT 1 FROM auto_vehicle_location_binding binding
      WHERE binding.vehicle_id=a.vehicle_id AND binding.location_id=w.location_id
    )
"""


def _actor(conn,token):
    actor=session_subject(conn,token,for_mutation=True)
    if actor is None:
        raise CustomerOrderDenied("AUTH_REQUIRED")
    role=conn.execute("""
       SELECT id FROM user_role WHERE user_id=%s AND role_code='CLIENT'
         AND revoked_at IS NULL
         AND (expires_at IS NULL OR expires_at>now())
       FOR SHARE
    """,(actor,)).fetchone()
    if role is None:
        raise CustomerOrderDenied("FORBIDDEN")
    return actor


def _card(row):
    order,org,loc,created,make,model,year,vin,starts,ends,canceled=row
    return {
      "id":str(order),"organizationId":str(org),"locationId":str(loc),
      "createdAt":created.isoformat(),
      "vehicle":{"make":make,"model":model,"modelYear":year,
                 "vinLast4":vin[-4:] if vin else None},
      "appointment":{"startsAt":starts.isoformat(),"endsAt":ends.isoformat(),
                     "status":"CANCELED" if canceled else "SCHEDULED"},
    }


_SELECT = """
 SELECT w.id,w.organization_id,w.location_id,w.created_at,
        v.make,v.model,v.model_year,v.vin,
        b.starts_at,b.ends_at,b.canceled_at
"""


def list_customer_orders(conn,token,*,limit=20,cursor=None):
    if type(limit) is not int or not 1<=limit<=30:
        raise CustomerOrderInvalid("INVALID_INPUT")
    if cursor is not None and (type(cursor) is not str or len(cursor)>256):
        raise CustomerOrderInvalid("INVALID_INPUT")
    with conn.transaction():
        actor=_actor(conn,token)
        scope=hashlib.sha256(("customer-orders:v1:"+str(actor)).encode()).hexdigest()[:32]
        position=None
        if cursor:
            try:
                position=decode_cursor(cursor,scope,token)
            except ValueError:
                raise CustomerOrderInvalid("INVALID_CURSOR") from None
        predicate=" AND (w.created_at,w.id)<(%s,%s)" if position else ""
        params=[actor]
        if position:
            params.extend(position)
        params.append(limit+1)
        rows=conn.execute(
            _SELECT+_BASE+predicate+" ORDER BY w.created_at DESC,w.id DESC LIMIT %s",
            tuple(params)
        ).fetchall()
        page=rows[:limit]
        next_cursor=(encode_cursor(page[-1][3],page[-1][0],scope,token)
                     if len(rows)>limit else None)
        return {"items":[_card(row) for row in page],"nextCursor":next_cursor,
                "syntheticOnly":True,"releaseAllowed":False}


def customer_order_details(conn,token,order):
    if type(order) is not UUID:
        raise CustomerOrderInvalid("INVALID_INPUT")
    with conn.transaction():
        actor=_actor(conn,token)
        row=conn.execute(
            _SELECT+_BASE+" AND w.id=%s FOR SHARE OF w,a,proof,v,org,loc,b",
            (actor,order),
        ).fetchone()
        if row is None:
            raise CustomerOrderDenied("NOT_FOUND")
        card=_card(row)
        quote=_estimate_state(conn,order)
        inspection=_snapshot(conn,order)
        execution=_state(conn,order)
        # Customer only sees their own WorkOrder's approved labor. Never return
        # internals of warehouse lots, workers, role grants, customer identities.
        work=[]
        for line in execution["labor"]:
            service=next((s for s in execution["requiredServices"]
                          if s["serviceId"]==line["serviceId"]),None)
            if service:
                work.append({"serviceId":line["serviceId"],"title":service["title"],
                             "actualMinutes":line["actualMinutes"]})
        docs=conn.execute("""
          SELECT id,fingerprint,created_at
          FROM auto_order_demo_handover_preview WHERE work_order_id=%s
        """,(order,)).fetchone()
        card.update({
          "estimates":quote["items"],"latestVersion":quote["latestVersion"],
          "inspection":inspection["inspection"],
          "qcStatus":inspection["status"],
          "workStatus":execution["status"],
          "labor":work,
          "document":None if docs is None else {
             "id":str(docs[0]),"sha256":docs[1].strip(),
             "createdAt":docs[2].isoformat(),"type":"SYNTHETIC_PRE_HANDOVER_PREVIEW",
          },
          "canDecide":bool(quote["items"] and
              quote["items"][-1]["decision"] is None and
              not row[-1]),
          "syntheticOnly":True,"legalGatePassed":False,"releaseAllowed":False,
        })
        return card
