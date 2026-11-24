"""Опрос внутри кабинета, без адресатов, сообщений или внешней отправки."""
from datetime import datetime, timezone, timedelta, date
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import psycopg

from .auto_crm_leads import _authority, _lead, LeadDenied, LeadConflict
from .auto_customer_orders import _actor, CustomerOrderDenied
from .auto_org_authority import organization_role_allows
from .auto_crm_funnel import _day, FunnelInvalid

REASONS=("QUALITY","TIMING","COMMUNICATION","NONE","OTHER")


def _live(conn,order,lead):
    row=conn.execute("SELECT * FROM auto_crm_survey_live_scope(%s,%s)",(order,lead)).fetchone()
    if row is None:
        raise LeadDenied("FORBIDDEN")
    return row


def _staff(conn,token,org,loc,lead):
    actor,manager=_authority(conn,token,org,loc)
    conn.execute("SELECT id FROM auto_crm_lead WHERE id=%s AND organization_id=%s AND location_id=%s FOR UPDATE",
                 (lead,org,loc))
    _lead(conn,org,loc,lead,actor,manager)
    row=conn.execute("""
      SELECT w.id FROM auto_crm_lead_stage s JOIN auto_work_order w ON w.appointment_id=s.appointment_id
      WHERE s.lead_id=%s AND s.stage_code='BOOKED' AND w.organization_id=%s AND w.location_id=%s
      ORDER BY w.id LIMIT 2
    """,(lead,org,loc)).fetchall()
    if len(row)!=1:
        return actor,None
    order=row[0][0]
    conn.execute("SELECT id FROM auto_work_order WHERE id=%s FOR UPDATE",(order,))
    closed=conn.execute("SELECT 1 FROM auto_order_demo_document_event WHERE work_order_id=%s AND action='CLOSED_TECHNICAL'",(order,)).fetchone()
    if not closed:
        return actor,None
    _live(conn,order,lead)
    return actor,order


def _customer(conn,token,org,loc,order):
    from .auto_service_recommendations import _scope, RecommendationDenied
    # Use the same original-owner check as recommendation reads; lock lead
    # before work order so survey mutations share a consistent lock order.
    try:
        actor=_actor(conn,token)
        row=conn.execute("""
          SELECT s.lead_id FROM auto_crm_survey s
          JOIN auto_work_order w ON w.id=s.work_order_id
          JOIN auto_appointment a ON a.id=w.appointment_id
          JOIN auto_vehicle_customer_link p ON p.id=a.customer_link_id
            AND p.vehicle_id=a.vehicle_id AND p.verified_at IS NOT NULL
            AND p.verified_by IS NOT NULL AND p.revoked_at IS NULL
          JOIN account_customer_link acl ON acl.customer_id=p.customer_id
            AND acl.user_id=%s AND acl.revoked_at IS NULL
          WHERE w.id=%s AND w.organization_id=%s AND w.location_id=%s
          FOR SHARE OF p,acl
        """,(actor,order,org,loc)).fetchone()
        if row:
            conn.execute("SELECT id FROM auto_crm_lead WHERE id=%s FOR UPDATE",(row[0],))
        _scope(conn,token,org,loc,order,customer=True)
    except (CustomerOrderDenied,RecommendationDenied):
        raise LeadDenied("FORBIDDEN") from None
    if row:
        scoped=_live(conn,order,row[0])
        if scoped[:2]!=(org,loc):
            raise LeadDenied("FORBIDDEN")
    return actor


def _state(conn,order,lead=None):
    survey=None
    if order is not None:
        row=conn.execute("""
          SELECT s.id,s.created_at,r.action,r.rating,r.reason_code,r.created_at
          FROM auto_crm_survey s LEFT JOIN auto_crm_survey_result r ON r.survey_id=s.id
          WHERE s.work_order_id=%s AND (%s::uuid IS NULL OR s.lead_id=%s)
        """,(order,lead,lead)).fetchone()
        if row:
            ident,created,action,rating,reason,answered=row
            deadline=created.astimezone(timezone.utc)+timedelta(days=30)
            survey={"id":str(ident),"createdAt":created.isoformat(),"expiresAt":deadline.isoformat(),
                    "status":action or ("EXPIRED" if datetime.now(timezone.utc)>=deadline else "OPEN"),
                    "rating":rating,"reasonCode":reason,"resolvedAt":answered.isoformat() if answered else None}
    return {"syntheticOnly":True,"sendAllowed":False,"releaseAllowed":False,
            "workOrderId":str(order) if order else None,"eligible":order is not None,"survey":survey}


def staff_survey(conn,token,org,loc,lead,*,key=None,action=None):
    with conn.transaction():
        actor,order=_staff(conn,token,org,loc,lead)
        if action is None:
            return _state(conn,order,lead)
        if order is None:
            raise LeadConflict("ORDER_NOT_CLOSED")
        if action=="INVITE":
            prior=conn.execute("SELECT lead_id,work_order_id,actor_id FROM auto_crm_survey WHERE request_key=%s",(key,)).fetchone()
            if prior:
                if prior!=(lead,order,actor):
                    raise LeadConflict("KEY_REUSED")
                return {**_state(conn,order,lead),"replayed":True}
            try:
                with conn.transaction():
                    conn.execute("INSERT INTO auto_crm_survey(id,lead_id,work_order_id,actor_id,request_key) VALUES(%s,%s,%s,%s,%s)",
                                 (uuid4(),lead,order,actor,key))
            except (psycopg.errors.IntegrityError,psycopg.errors.RaiseException):
                raise LeadConflict("SURVEY_CONFLICT") from None
            return {**_state(conn,order,lead),"replayed":False}
        if action!="CANCEL":
            raise LeadDenied("INVALID_INPUT")
        return _finish(conn,actor,order,lead,key,"CANCELED",None,None)


def customer_survey(conn,token,org,loc,order,*,key=None,rating=None,reason=None):
    with conn.transaction():
        actor=_customer(conn,token,org,loc,order)
        if key is None:
            state=_state(conn,order)
            return {**state,"eligible":state["survey"] is not None}
        if type(rating) is not int or not 1<=rating<=5 or reason not in REASONS:
            raise LeadDenied("INVALID_INPUT")
        return _finish(conn,actor,order,None,key,"ANSWERED",rating,reason)


def _finish(conn,actor,order,lead,key,action,rating,reason):
    state=_state(conn,order,lead)
    if state["survey"] is None:
        raise LeadConflict("NO_SURVEY")
    survey=UUID(state["survey"]["id"])
    prior=conn.execute("""
      SELECT survey_id,actor_id,action,rating,reason_code FROM auto_crm_survey_result WHERE request_key=%s
    """,(key,)).fetchone()
    if prior:
        if prior!=(survey,actor,action,rating,reason):
            raise LeadConflict("KEY_REUSED")
        return {**state,"replayed":True}
    if state["survey"]["status"]!="OPEN":
        raise LeadConflict("SURVEY_NOT_OPEN")
    try:
        with conn.transaction():
            conn.execute("""
              INSERT INTO auto_crm_survey_result(id,survey_id,actor_id,request_key,action,rating,reason_code)
              VALUES(%s,%s,%s,%s,%s,%s,%s)
            """,(uuid4(),survey,actor,key,action,rating,reason))
    except (psycopg.errors.IntegrityError,psycopg.errors.RaiseException):
        raise LeadConflict("SURVEY_CONFLICT") from None
    return {**_state(conn,order,lead),"replayed":False}


def survey_report(conn,token,org,loc,first,last):
    try:
        start,end=_day(first),_day(last)
        if not 1<=(end-start).days+1<=31 or end==date.max:
            raise ValueError()
    except (ValueError,FunnelInvalid):
        raise LeadDenied("INVALID_INPUT") from None
    with conn.transaction():
        if not organization_role_allows(conn,token,org,"MANAGER",loc,for_mutation=True):
            raise LeadDenied("FORBIDDEN")
        zone=conn.execute("SELECT timezone_name FROM auto_service_location WHERE id=%s AND organization_id=%s",(loc,org)).fetchone()
        try:
            tz=ZoneInfo(zone[0])
        except (TypeError,ValueError,ZoneInfoNotFoundError):
            raise LeadDenied("FORBIDDEN") from None
        end=end+timedelta(days=1)
        bounds=(datetime(start.year,start.month,start.day,tzinfo=tz),datetime(end.year,end.month,end.day,tzinfo=tz))
        # Each eligible survey is checked against the same original live proof.
        rows=conn.execute("""
          SELECT s.created_at,r.action,r.rating,r.reason_code
          FROM auto_crm_survey s JOIN auto_crm_lead l ON l.id=s.lead_id
          JOIN LATERAL auto_crm_survey_live_scope(s.work_order_id,s.lead_id) scope ON true
          LEFT JOIN auto_crm_survey_result r ON r.survey_id=s.id
          WHERE l.organization_id=%s AND l.location_id=%s AND s.created_at>=%s AND s.created_at<%s
          ORDER BY s.created_at,s.id LIMIT 1001
        """,(org,loc,*bounds)).fetchall()
        if len(rows)>1000:
            raise LeadConflict("REPORT_LIMIT")
        statuses={"OPEN":0,"ANSWERED":0,"CANCELED":0,"EXPIRED":0}
        ratings={str(n):0 for n in range(1,6)}
        reasons={reason:0 for reason in REASONS}
        now=datetime.now(timezone.utc)
        for created,action,rating,reason in rows:
            status=action or ("EXPIRED" if now>=created.astimezone(timezone.utc)+timedelta(days=30) else "OPEN")
            statuses[status]+=1
            if status=="ANSWERED":
                ratings[str(rating)]+=1
                reasons[reason]+=1
        return {"syntheticOnly":True,"sendAllowed":False,"releaseAllowed":False,
                "fromDay":first,"toDay":last,"timeZone":zone[0],"total":len(rows),
                "statuses":statuses,"ratings":ratings,"reasons":reasons,
                "averageHundredths":sum(int(k)*v for k,v in ratings.items())*100//statuses["ANSWERED"]
                    if statuses["ANSWERED"] else None}
