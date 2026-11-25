"""Scoped date-based service recommendation queue; no delivery or customer identity."""
from datetime import datetime,timedelta,timezone,time
from hashlib import sha256
from uuid import UUID
from zoneinfo import ZoneInfo,ZoneInfoNotFoundError

from .auto_org_authority import organization_role_allows
from .auto_document_access import require_document_access,DocumentPolicyDenied
from .keyset_cursor import encode_cursor,decode_cursor

MODES=("ALL","OVERDUE","UPCOMING")
REASONS=("ALL","PERIODIC","DEFERRED","FOLLOWUP")
PAGE=25


class QueueDenied(ValueError):
    pass


class QueueInvalid(ValueError):
    pass


def read_maintenance_queue(conn,token,org,loc,mode="ALL",horizon=30,cursor=None,reason="ALL"):
    if (type(org) is not UUID or type(loc) is not UUID or mode not in MODES
        or type(horizon) is not int or horizon not in (7,30,90) or reason not in REASONS):
        raise QueueInvalid("INVALID_INPUT")
    with conn.transaction():
        if not organization_role_allows(conn,token,org,"MANAGER",loc,for_mutation=True):
            raise QueueDenied("FORBIDDEN")
        try:
            cutoff=require_document_access(conn,token,org,loc,
                ("MANAGER","INTAKE","MECHANIC"),"INTAKE_ACT","READ")
        except DocumentPolicyDenied:
            raise QueueDenied("FORBIDDEN") from None
        branch=conn.execute("""
          SELECT timezone_name FROM auto_service_location
          WHERE id=%s AND organization_id=%s AND disabled_at IS NULL FOR SHARE
        """,(loc,org)).fetchone()
        if not branch:
            raise QueueDenied("FORBIDDEN")
        try:
            today=datetime.now(ZoneInfo(branch[0])).date()
        except (ZoneInfoNotFoundError,TypeError,ValueError):
            raise QueueDenied("FORBIDDEN") from None
        end=today+timedelta(days=horizon)
        scope=sha256(f"maintenance:{org}:{loc}:{mode}:{horizon}:{today}:{reason}".encode()).hexdigest()[:32]
        try:
            position=decode_cursor(cursor,scope,token) if cursor is not None else None
        except ValueError:
            raise QueueInvalid("INVALID_CURSOR") from None
        after=(position[0].date(),position[0].date(),position[1]) if position else (None,None,None)
        rows=conn.execute("""
          SELECT r.id,w.id,r.service_code,r.service_title,r.reason_code,
                 r.baseline_km,r.due_date,r.due_km
          FROM auto_service_recommendation r
          JOIN auto_work_order w ON w.id=r.work_order_id
          JOIN auto_appointment a ON a.id=w.appointment_id
            AND a.organization_id=w.organization_id AND a.location_id=w.location_id
          JOIN auto_bay_booking origin ON origin.id=COALESCE((
            SELECT x.new_booking_id FROM auto_appointment_reschedule x
            WHERE x.appointment_id=a.id ORDER BY x.created_at DESC,x.id DESC LIMIT 1
          ),a.booking_id) AND origin.organization_id=w.organization_id
            AND origin.location_id=w.location_id AND origin.canceled_at IS NULL
          WHERE w.organization_id=%s AND w.location_id=%s
            AND (%s::timestamptz IS NULL OR w.created_at >= %s::timestamptz)
            AND (%s='ALL' OR r.reason_code=%s)
            AND r.due_date<=%s
            AND (%s<>'OVERDUE' OR r.due_date<%s)
            AND (%s<>'UPCOMING' OR r.due_date>=%s)
            AND (%s::date IS NULL OR (r.due_date,r.id)>(%s::date,%s::uuid))
            AND NOT EXISTS(SELECT 1 FROM auto_service_recommendation_withdrawal x
                           WHERE x.recommendation_id=r.id)
            AND EXISTS(
              SELECT 1 FROM auto_vehicle_customer_link p
              JOIN auto_vehicle_location_binding b ON b.vehicle_id=p.vehicle_id
                AND b.location_id=w.location_id
              WHERE p.id=a.customer_link_id AND p.vehicle_id=a.vehicle_id
                AND p.verified_at IS NOT NULL AND p.verified_by IS NOT NULL AND p.revoked_at IS NULL
              FOR SHARE OF p,b
            )
            AND NOT EXISTS(
              SELECT 1 FROM auto_recommendation_visit v
              JOIN auto_appointment visit ON visit.id=v.appointment_id
              JOIN auto_bay_booking booking ON booking.id=COALESCE((
                SELECT x.new_booking_id FROM auto_appointment_reschedule x
                WHERE x.appointment_id=visit.id ORDER BY x.created_at DESC,x.id DESC LIMIT 1
              ),visit.booking_id)
              WHERE v.recommendation_id=r.id AND booking.canceled_at IS NULL
            )
          ORDER BY r.due_date,r.id LIMIT %s
        """,(org,loc,cutoff,cutoff,reason,reason,end,mode,today,mode,today,*after,PAGE+1)).fetchall()
        page=rows[:PAGE]
        return {"syntheticOnly":True,"sendAllowed":False,"basis":"UNBOOKED_ACTIVE_DUE_DATE",
                "organizationId":str(org),"locationId":str(loc),"localDay":today.isoformat(),
                "timeZone":branch[0],"mode":mode,"horizonDays":horizon,"reasonCode":reason,
                "items":[{"recommendationId":str(i),"workOrderId":str(w),
                          "serviceCode":code,"title":title,"reasonCode":reason,
                          "baselineKm":baseline,"dueDate":due.isoformat(),"dueKm":km,
                          "status":"OVERDUE" if due<today else "UPCOMING"}
                         for i,w,code,title,reason,baseline,due,km in page],
                "nextCursor":encode_cursor(datetime.combine(page[-1][6],time.min,tzinfo=timezone.utc),
                    page[-1][0],scope,token) if len(rows)>PAGE else None}
