"""Закрытый план ТО: отдельные рекомендации, не продажи и не бронирование."""
from datetime import date, datetime, timedelta, timezone
from uuid import UUID, uuid4

import psycopg

from .auto_workshop_qc import _actor_and_order, InspectionDenied
from .auto_customer_orders import _actor as customer_actor, CustomerOrderDenied
from .auto_org_authority import organization_role_allows

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
      FOR SHARE OF a,p,l,o,b,binding
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
        if not customer:
            item.update(actorId=str(actor),withdrawnBy=str(withdrawer) if withdrawer else None)
        items.append(item)
    inspection=conn.execute("SELECT odometer_km FROM auto_work_order_inspection WHERE work_order_id=%s",
                            (order,)).fetchone()
    catalog=[]
    if can_manage:
        catalog=conn.execute("""
          SELECT id,code,title FROM auto_service_catalog
          WHERE organization_id=%s AND location_id=%s ORDER BY code,id LIMIT 101
        """,(org,loc)).fetchall()
        if len(catalog)>100:
            # Список выбора не должен молча скрывать услуги.
            raise RecommendationConflict("CATALOG_LIMIT")
    return {"syntheticOnly":True,"releaseAllowed":False,"sendAllowed":False,
            "workOrderId":str(order),"canManage":can_manage,
            "baselineKm":inspection[0] if inspection else None,
            "services":[{"id":str(i),"code":c,"title":t} for i,c,t in catalog],
            "items":items}


def read_recommendations(conn,token,org,loc,order,customer=False):
    with conn.transaction():
        _scope(conn,token,org,loc,order,customer=customer)
        manager=not customer and organization_role_allows(
            conn,token,org,"MANAGER",loc,for_mutation=True)
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
