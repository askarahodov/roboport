"""Customer-owned automotive WorkOrders: scoped discovery, history and consent context.

No VIN/full customer identity, stock suppliers, legacy ticket data or production
endpoints. Unlike vehicle-level history, access is bound to the *exact* original
verified vehicle/customer link on the appointment. A new owner of the same car
cannot inherit the previous customer's order or documents.
"""
import csv
import hashlib
import io
import json
from datetime import date
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


class CustomerOrderLimit(ValueError):
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


def _filters(search, status, from_day, to_day, vehicle_id):
    if (type(search) is not str or len(search)>64
        or any(ord(c)<32 or ord(c)==127 for c in search)
        or status not in ("ALL","SCHEDULED","CANCELED")):
        raise CustomerOrderInvalid("INVALID_INPUT")
    def day(value):
        if value is None:
            return None
        if type(value) is not str or len(value)!=10:
            raise CustomerOrderInvalid("INVALID_INPUT")
        try:
            parsed=date.fromisoformat(value)
        except ValueError:
            raise CustomerOrderInvalid("INVALID_INPUT") from None
        if parsed.isoformat()!=value:
            raise CustomerOrderInvalid("INVALID_INPUT")
        return parsed
    first,last=day(from_day),day(to_day)
    if first is not None and last is not None and first>last:
        raise CustomerOrderInvalid("INVALID_INPUT")
    if vehicle_id is not None and type(vehicle_id) is not UUID:
        raise CustomerOrderInvalid("INVALID_INPUT")
    return search.strip(),status,first,last,vehicle_id


def _filtered_rows(conn,actor,filters,*,limit,position=None):
    search,status,first,last,vehicle_id=filters
    predicates=[]
    params=[actor]
    if search:
        # Literal substring of the catalog make/model, never VIN or customer ID.
        predicates.append("strpos(lower(v.make || ' ' || v.model),lower(%s))>0")
        params.append(search)
    if status!="ALL":
        predicates.append("b.canceled_at IS NULL" if status=="SCHEDULED" else
                          "b.canceled_at IS NOT NULL")
    if first is not None:
        predicates.append("(b.starts_at AT TIME ZONE loc.timezone_name)::date >= %s")
        params.append(first)
    if last is not None:
        predicates.append("(b.starts_at AT TIME ZONE loc.timezone_name)::date <= %s")
        params.append(last)
    if vehicle_id is not None:
        # UUID is a selector, not authority: original proof/customer ACL remains in _BASE.
        predicates.append("a.vehicle_id=%s")
        params.append(vehicle_id)
    if position:
        predicates.append("(w.created_at,w.id)<(%s,%s)")
        params.extend(position)
    suffix="".join(" AND "+p for p in predicates)
    params.append(limit)
    return conn.execute(_SELECT+_BASE+suffix+
                        " ORDER BY w.created_at DESC,w.id DESC LIMIT %s",
                        tuple(params)).fetchall()


def list_customer_orders(conn,token,*,limit=20,cursor=None,search="",status="ALL",
                         from_day=None,to_day=None,vehicle_id=None):
    if type(limit) is not int or not 1<=limit<=30:
        raise CustomerOrderInvalid("INVALID_INPUT")
    if cursor is not None and (type(cursor) is not str or not 1<=len(cursor)<=256):
        raise CustomerOrderInvalid("INVALID_INPUT")
    filters=_filters(search,status,from_day,to_day,vehicle_id)
    with conn.transaction():
        actor=_actor(conn,token)
        scope_key="customer-orders:v1:"+str(actor)
        if filters[-1] is not None:
            scope_key+=":"+json.dumps(filters,default=str,ensure_ascii=False,separators=(",",":"))
        elif filters[:4]!=("", "ALL", None, None):
            scope_key+=":"+json.dumps(filters[:4],default=str,ensure_ascii=False,separators=(",",":"))
        scope=hashlib.sha256(scope_key.encode()).hexdigest()[:32]
        position=None
        if cursor:
            try:
                position=decode_cursor(cursor,scope,token)
            except ValueError:
                raise CustomerOrderInvalid("INVALID_CURSOR") from None
        rows=_filtered_rows(conn,actor,filters,limit=limit+1,position=position)
        page=rows[:limit]
        next_cursor=(encode_cursor(page[-1][3],page[-1][0],scope,token)
                     if len(rows)>limit else None)
        return {"items":[_card(row) for row in page],"nextCursor":next_cursor,
                "syntheticOnly":True,"releaseAllowed":False}


EXPORT_LIMIT=500
CSV_HEADER=("orderId","organizationId","locationId","createdAt","make","model",
            "modelYear","vinLast4","startsAt","endsAt","appointmentStatus")


def _csv_text(value):
    # No executable spreadsheet cells; escape even leading whitespace/control.
    value="".join(" " if ord(c)<32 or ord(c)==127 else c for c in str(value))
    return "'"+value if value.lstrip().startswith(("=","+","-","@")) else value


def export_customer_orders(conn,token,*,search="",status="ALL",from_day=None,to_day=None,
                           vehicle_id=None):
    filters=_filters(search,status,from_day,to_day,vehicle_id)
    with conn.transaction():
        actor=_actor(conn,token)
        rows=_filtered_rows(conn,actor,filters,limit=EXPORT_LIMIT+1)
        if len(rows)>EXPORT_LIMIT:
            raise CustomerOrderLimit("EXPORT_LIMIT")
        stream=io.StringIO(newline="")
        writer=csv.writer(stream,lineterminator="\r\n",quoting=csv.QUOTE_ALL)
        writer.writerow(CSV_HEADER)
        for row in rows:
            card=_card(row)
            vehicle,appointment=card["vehicle"],card["appointment"]
            writer.writerow([card["id"],card["organizationId"],card["locationId"],
                             card["createdAt"],_csv_text(vehicle["make"]),
                             _csv_text(vehicle["model"]),
                             vehicle["modelYear"] if vehicle["modelYear"] is not None else "",
                             vehicle["vinLast4"] or "",appointment["startsAt"],
                             appointment["endsAt"],appointment["status"]])
        return stream.getvalue()


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
              (quote["items"][-1]["clarificationId"] is None or
               quote["items"][-1]["clarificationResolution"]=="EXPLAINED") and
              not row[-1]),
          "syntheticOnly":True,"legalGatePassed":False,"releaseAllowed":False,
        })
        return card
