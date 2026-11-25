"""Закрытый план ТО и явно подтверждённая запись клиента на повторный визит."""
from datetime import date, datetime, timedelta, timezone
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import psycopg

from .auto_workshop_qc import _actor_and_order, InspectionDenied, _inspection_policy
from .auto_document_access import has_document_access
from .auto_customer_orders import _actor as customer_actor, CustomerOrderDenied
from .auto_org_authority import organization_role_allows
from .auto_appointment_service import (create_appointment, AppointmentDenied,
    AppointmentConflict, AppointmentKeyConflict, AppointmentDurationInvalid)
from .auto_slot_candidates import candidate_slots, SlotsDenied

REASONS=("PERIODIC","DEFERRED","FOLLOWUP")


class RecommendationDenied(ValueError):
    pass


class RecommendationConflict(ValueError):
    pass


def _scope(conn,token,org,loc,order,*,customer=False,write=False):
    try:
        actor=(customer_actor(conn,token) if customer else
               _actor_and_order(conn,token,org,loc,order,
                   ("MANAGER",) if write else ("MANAGER","INTAKE","MECHANIC")))
        if not customer:
            _inspection_policy(conn,token,org,loc,order,
                               write_roles=("MANAGER",) if write else ())
    except (InspectionDenied,CustomerOrderDenied):
        raise RecommendationDenied("FORBIDDEN") from None
    # Проверяем именно исходную связь заказчика, а не текущего владельца VIN.
    # Блокируем строки связи, филиала и брони до окончания операции.
    row=conn.execute("""
      SELECT w.id,p.customer_id
      FROM auto_work_order w
      JOIN auto_appointment a ON a.id=w.appointment_id
        AND a.organization_id=w.organization_id AND a.location_id=w.location_id
      JOIN auto_vehicle_customer_link p ON p.id=a.customer_link_id
        AND p.vehicle_id=a.vehicle_id AND p.verified_at IS NOT NULL
        AND p.verified_by IS NOT NULL AND p.revoked_at IS NULL
      JOIN auto_vehicle_location_binding binding ON binding.vehicle_id=a.vehicle_id
        AND binding.location_id=w.location_id
      JOIN auto_organization o ON o.id=w.organization_id AND o.disabled_at IS NULL
      JOIN auto_service_location l ON l.id=w.location_id
        AND l.organization_id=o.id AND l.disabled_at IS NULL
      JOIN auto_bay_booking b ON b.id=COALESCE((
        SELECT r.new_booking_id FROM auto_appointment_reschedule r
        WHERE r.appointment_id=a.id ORDER BY r.created_at DESC,r.id DESC LIMIT 1
      ),a.booking_id) AND b.organization_id=w.organization_id
        AND b.location_id=w.location_id AND b.canceled_at IS NULL
      WHERE w.id=%s AND w.organization_id=%s AND w.location_id=%s
      FOR UPDATE OF w FOR SHARE OF a,p,l,o,b,binding
    """,(order,org,loc)).fetchone()
    if row is None:
        raise RecommendationDenied("FORBIDDEN")
    if customer and not conn.execute("""
      SELECT 1 FROM account_customer_link
      WHERE user_id=%s AND customer_id=%s AND revoked_at IS NULL FOR SHARE
    """,(actor,row[1])).fetchone():
        raise RecommendationDenied("FORBIDDEN")
    return actor


def _state(conn,org,loc,order,*,can_manage=False,customer=False):
    rows=conn.execute("""
      SELECT r.id,r.service_id,r.service_code,r.service_title,r.reason_code,
             r.baseline_km,r.due_date,r.due_km,r.created_at,
             r.actor_id,x.created_at,x.actor_id
      FROM auto_service_recommendation r
      LEFT JOIN auto_service_recommendation_withdrawal x ON x.recommendation_id=r.id
      WHERE r.work_order_id=%s ORDER BY r.due_date,r.created_at,r.id
      LIMIT 101
    """,(order,)).fetchall()
    if len(rows)>100:
        raise RecommendationConflict("LIMIT")
    items=[]
    for ident,service,code,title,reason,baseline,due,km,created,actor,withdrawn,withdrawer in rows:
        item={"id":str(ident),"serviceId":str(service),"serviceCode":code,
              "title":title,"reasonCode":reason,"baselineKm":baseline,
              "dueDate":due.isoformat(),"dueKm":km,"createdAt":created.isoformat(),
              "status":"WITHDRAWN" if withdrawn else "ACTIVE",
              "withdrawnAt":withdrawn.isoformat() if withdrawn else None}
        item["visit"]=_latest_visit(conn,ident)
        if not customer:
            item.update(actorId=str(actor),withdrawnBy=str(withdrawer) if withdrawer else None)
        items.append(item)
    inspection=conn.execute("SELECT odometer_km FROM auto_work_order_inspection WHERE work_order_id=%s",
                            (order,)).fetchone()
    catalog=_catalog(conn,org,loc,"") if can_manage else {"items":[],"truncated":False}
    return {"syntheticOnly":True,"releaseAllowed":False,"sendAllowed":False,
            "workOrderId":str(order),"canManage":can_manage,
            "baselineKm":inspection[0] if inspection else None,
            "services":catalog["items"],"servicesTruncated":catalog["truncated"],
            "items":items}


def _catalog(conn,org,loc,query):
    rows=conn.execute("""
      SELECT id,code,title FROM auto_service_catalog
      WHERE organization_id=%s AND location_id=%s
        AND (strpos(lower(code),lower(%s))>0 OR strpos(lower(title),lower(%s))>0 OR %s='')
      ORDER BY code,id LIMIT 101
    """,(org,loc,query,query,query)).fetchall()
    return {"items":[{"id":str(i),"code":c,"title":t} for i,c,t in rows[:100]],
            "truncated":len(rows)>100}


def search_recommendation_services(conn,token,org,loc,order,query):
    if type(query) is not str or len(query)>80 or any(ord(ch)<32 for ch in query):
        raise RecommendationDenied("INVALID_INPUT")
    with conn.transaction():
        _scope(conn,token,org,loc,order,write=True)
        return {"syntheticOnly":True,"workOrderId":str(order),**_catalog(conn,org,loc,query.strip())}


def read_recommendations(conn,token,org,loc,order,customer=False):
    with conn.transaction():
        _scope(conn,token,org,loc,order,customer=customer)
        manager=not customer and organization_role_allows(
            conn,token,org,"MANAGER",loc,for_mutation=True)
        if manager:
            manager=has_document_access(conn,token,org,loc,("MANAGER",),
                                        "INTAKE_ACT","WRITE",order=order)
        return _state(conn,org,loc,order,can_manage=manager,customer=customer)


def create_recommendation(conn,token,org,loc,order,*,key,service,reason,due,km):
    if (type(key) is not UUID or type(service) is not UUID or reason not in REASONS
        or type(due) is not date or (km is not None and
            (type(km) is not int or not 0<=km<=2000000))):
        raise RecommendationDenied("INVALID_INPUT")
    with conn.transaction():
        actor=_scope(conn,token,org,loc,order,write=True)
        prior=conn.execute("""
          SELECT work_order_id,service_id,actor_id,reason_code,due_date,due_km
          FROM auto_service_recommendation WHERE request_key=%s
        """,(key,)).fetchone()
        if prior:
            if prior!=(order,service,actor,reason,due,km):
                raise RecommendationConflict("KEY_REUSED")
            return {"replayed":True,**_state(conn,org,loc,order,can_manage=True)}
        today=datetime.now(timezone.utc).date()
        if not today<due<=today+timedelta(days=730):
            raise RecommendationDenied("INVALID_INPUT")
        baseline=conn.execute("SELECT odometer_km FROM auto_work_order_inspection WHERE work_order_id=%s",
                              (order,)).fetchone()
        if baseline is None:
            raise RecommendationConflict("INSPECTION_REQUIRED")
        if km is not None and km<=baseline[0]:
            raise RecommendationDenied("INVALID_INPUT")
        catalog=conn.execute("""
          SELECT code,title FROM auto_service_catalog
          WHERE id=%s AND organization_id=%s AND location_id=%s FOR SHARE
        """,(service,org,loc)).fetchone()
        if catalog is None:
            raise RecommendationDenied("FORBIDDEN")
        try:
            with conn.transaction():
                conn.execute("""
                  INSERT INTO auto_service_recommendation
                    (id,work_order_id,service_id,actor_id,request_key,service_code,
                     service_title,reason_code,baseline_km,due_date,due_km)
                  VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,(uuid4(),order,service,actor,key,*catalog,reason,baseline[0],due,km))
        except (psycopg.errors.UniqueViolation,psycopg.errors.RaiseException,
                psycopg.errors.CheckViolation):
            raise RecommendationConflict("RECOMMENDATION_CONFLICT") from None
        return {"replayed":False,**_state(conn,org,loc,order,can_manage=True)}


def withdraw_recommendation(conn,token,org,loc,order,*,key,ident):
    if type(key) is not UUID or type(ident) is not UUID:
        raise RecommendationDenied("INVALID_INPUT")
    with conn.transaction():
        actor=_scope(conn,token,org,loc,order,write=True)
        if not conn.execute("""
          SELECT id FROM auto_service_recommendation WHERE id=%s AND work_order_id=%s
          FOR SHARE
        """,(ident,order)).fetchone():
            raise RecommendationDenied("FORBIDDEN")
        prior=conn.execute("""
          SELECT recommendation_id,actor_id FROM auto_service_recommendation_withdrawal
          WHERE request_key=%s
        """,(key,)).fetchone()
        if prior:
            if prior!=(ident,actor):
                raise RecommendationConflict("KEY_REUSED")
            return {"replayed":True,**_state(conn,org,loc,order,can_manage=True)}
        try:
            with conn.transaction():
                conn.execute("""
                  INSERT INTO auto_service_recommendation_withdrawal
                    (id,recommendation_id,actor_id,request_key) VALUES(%s,%s,%s,%s)
                """,(uuid4(),ident,actor,key))
        except psycopg.errors.UniqueViolation:
            raise RecommendationConflict("ALREADY_WITHDRAWN") from None
        return {"replayed":False,**_state(conn,org,loc,order,can_manage=True)}


def _latest_visit(conn,ident):
    row=conn.execute("""
      SELECT a.id,w.id,b.id,b.starts_at,b.ends_at,b.canceled_at
      FROM auto_recommendation_visit v
      JOIN auto_appointment a ON a.id=v.appointment_id
      JOIN auto_work_order w ON w.appointment_id=a.id
      JOIN auto_bay_booking b ON b.id=COALESCE((
        SELECT rr.new_booking_id FROM auto_appointment_reschedule rr
        WHERE rr.appointment_id=a.id ORDER BY rr.created_at DESC,rr.id DESC LIMIT 1
      ),a.booking_id)
      WHERE v.recommendation_id=%s ORDER BY v.created_at DESC,v.id DESC LIMIT 1
    """,(ident,)).fetchone()
    return None if row is None else {
        "appointmentId":str(row[0]),"workOrderId":str(row[1]),"bookingId":str(row[2]),
        "startsAt":row[3].isoformat(),"endsAt":row[4].isoformat(),
        "status":"CANCELED" if row[5] else "SCHEDULED",
    }


def _booking_context(conn,token,org,loc,order,ident):
    actor=_scope(conn,token,org,loc,order,customer=True)
    row=conn.execute("""
      SELECT r.service_id,a.customer_link_id,l.timezone_name
      FROM auto_service_recommendation r JOIN auto_work_order w ON w.id=r.work_order_id
      JOIN auto_appointment a ON a.id=w.appointment_id
      JOIN auto_service_location l ON l.id=w.location_id
      WHERE r.id=%s AND r.work_order_id=%s
    """,(ident,order)).fetchone()
    if row is None:
        raise RecommendationDenied("FORBIDDEN")
    if conn.execute("SELECT 1 FROM auto_service_recommendation_withdrawal WHERE recommendation_id=%s",
                    (ident,)).fetchone():
        raise RecommendationConflict("RECOMMENDATION_WITHDRAWN")
    return actor,*row


def repeat_visit_options(conn,token,org,loc,order,ident,*,bay=None,day=None):
    with conn.transaction():
        _,service,link,zone=_booking_context(conn,token,org,loc,order,ident)
        visit=_latest_visit(conn,ident)
        if visit and visit["status"]=="SCHEDULED":
            raise RecommendationConflict("VISIT_ALREADY_BOOKED")
        if bay is None and day is None:
            bays=conn.execute("""
              SELECT id,code FROM auto_work_bay
              WHERE organization_id=%s AND location_id=%s AND disabled_at IS NULL
              ORDER BY code,id LIMIT 101
            """,(org,loc)).fetchall()
            if len(bays)>100:
                raise RecommendationConflict("BAY_LIMIT")
            return {"syntheticOnly":True,"timeZone":zone,
                    "bays":[{"id":str(i),"code":c} for i,c in bays]}
        if type(bay) is not UUID or type(day) is not date:
            raise RecommendationDenied("INVALID_INPUT")
        try:
            tz=ZoneInfo(zone)
            start=datetime.combine(day,datetime.min.time(),tzinfo=tz)
            end=datetime.combine(day+timedelta(days=1),datetime.min.time(),tzinfo=tz)
            if not datetime.now(tz).date()<=day<=datetime.now(tz).date()+timedelta(days=730):
                raise RecommendationDenied("INVALID_INPUT")
            slots=candidate_slots(conn,token,org,loc,bay,start,day_end=end,
                                  customer_link=link,service_ids=(service,))
        except (SlotsDenied,ZoneInfoNotFoundError):
            raise RecommendationDenied("FORBIDDEN") from None
        except OverflowError:
            raise RecommendationDenied("INVALID_INPUT") from None
        return {"syntheticOnly":True,"timeZone":zone,"guaranteed":False,"items":slots}


def book_repeat_visit(conn,token,org,loc,order,ident,*,key,bay,starts,ends):
    if (type(key) is not UUID or type(bay) is not UUID
        or any(type(x) is not datetime or x.tzinfo is None or x.utcoffset() is None
               for x in (starts,ends)) or ends.astimezone(timezone.utc)<=starts.astimezone(timezone.utc)
        or ends.astimezone(timezone.utc)-starts.astimezone(timezone.utc)>timedelta(days=1)):
        raise RecommendationDenied("INVALID_INPUT")
    with conn.transaction():
        actor,service,link,zone=_booking_context(conn,token,org,loc,order,ident)
        prior=conn.execute("""
          SELECT v.recommendation_id,v.actor_id,v.appointment_id,v.booking_id,
                 b.bay_id,b.starts_at,b.ends_at
          FROM auto_recommendation_visit v JOIN auto_bay_booking b ON b.id=v.booking_id
          WHERE v.request_key=%s
        """,(key,)).fetchone()
        visit=_latest_visit(conn,ident)
        if prior:
            if (prior[0]!=ident or prior[1]!=actor or prior[4:]!=(bay,starts,ends)
                or not visit or visit["appointmentId"]!=str(prior[2])
                or visit["bookingId"]!=str(prior[3]) or visit["status"]!="SCHEDULED"):
                raise RecommendationConflict("KEY_REUSED")
            return {"replayed":True,"syntheticOnly":True,"visit":visit}
        if visit and visit["status"]=="SCHEDULED":
            raise RecommendationConflict("VISIT_ALREADY_BOOKED")
        try:
            local=starts.astimezone(ZoneInfo(zone)).date()
        except (ZoneInfoNotFoundError,OverflowError):
            raise RecommendationDenied("INVALID_INPUT") from None
        # Запись разрешена только на окно, выданное действующим планировщиком,
        # с длительностью рекомендованной услуги, а не произвольным интервалом.
        candidates=repeat_visit_options(conn,token,org,loc,order,ident,bay=bay,day=local)["items"]
        if not any(datetime.fromisoformat(x["startsAt"])==starts and
                   datetime.fromisoformat(x["endsAt"])==ends for x in candidates):
            raise RecommendationConflict("VISIT_CONFLICT")
        try:
            with conn.transaction():
                appointment,booking,_=create_appointment(
                    conn,token,org,loc,bay,link,starts,ends,customer_mode=True,
                    service_ids=(service,))
                conn.execute("""
                  INSERT INTO auto_recommendation_visit
                    (id,recommendation_id,appointment_id,booking_id,actor_id,request_key)
                  VALUES(%s,%s,%s,%s,%s,%s)
                """,(uuid4(),ident,appointment,booking,actor,key))
        except AppointmentDurationInvalid:
            raise RecommendationDenied("INVALID_INPUT") from None
        except AppointmentDenied:
            raise RecommendationDenied("FORBIDDEN") from None
        except (AppointmentConflict,AppointmentKeyConflict,psycopg.errors.UniqueViolation,
                psycopg.errors.RaiseException,psycopg.errors.DeadlockDetected):
            raise RecommendationConflict("VISIT_CONFLICT") from None
        return {"replayed":False,"syntheticOnly":True,"visit":_latest_visit(conn,ident)}
