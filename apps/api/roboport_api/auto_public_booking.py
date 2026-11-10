"""Anonymous no-PII dev requests, original-client claim, staff booking/materials/CRM.

This is not a public production service: HTTPS loopback, bounded synthetic
data only. No VIN/contact capture, no SMS, email, CAPTCHA provider or real consent.
"""
from datetime import datetime, date, timedelta, timezone
from hashlib import sha256
from secrets import randbelow
from uuid import UUID,uuid4,uuid5,NAMESPACE_URL
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import psycopg

from .auto_org_authority import organization_role_allows
from .auto_appointment_service import (
    _customer_scope, create_appointment, AppointmentDenied, AppointmentConflict,
    AppointmentStockUnavailable, AppointmentKeyConflict, AppointmentDurationInvalid,
    AppointmentHoursClosed,
)
from .session_lookup import session_subject


class PublicBookingDenied(ValueError): pass
class PublicBookingInvalid(ValueError): pass
class PublicBookingConflict(ValueError): pass


def _id(*items):
    if any(type(v) is not UUID for v in items):
        raise PublicBookingInvalid("INVALID_INPUT")


def _staff(conn,token,org,loc,roles):
    _id(org,loc)
    if type(token) is not str or not token:
        raise PublicBookingDenied("FORBIDDEN")
    actor=session_subject(conn,token,for_mutation=True)
    if actor is None or not all(organization_role_allows(
        conn,token,org,role,loc,for_mutation=True
    ) for role in roles):
        raise PublicBookingDenied("FORBIDDEN")
    return actor


def _offer(conn,ident,*,lock=False):
    _id(ident)
    row=conn.execute("""
       SELECT p.id,p.organization_id,p.location_id,p.bay_id,p.service_id,
              p.actor_id,p.policy_version,service.title,service.duration_minutes,
              bay.code,location.timezone_name
       FROM auto_public_booking_offer p
       JOIN auto_organization org ON org.id=p.organization_id AND org.disabled_at IS NULL
       JOIN auto_service_location location ON location.id=p.location_id
         AND location.organization_id=org.id AND location.disabled_at IS NULL
       JOIN auto_work_bay bay ON bay.id=p.bay_id
         AND bay.organization_id=org.id AND bay.location_id=location.id
         AND bay.disabled_at IS NULL
       JOIN auto_service_catalog service ON service.id=p.service_id
         AND service.organization_id=org.id AND service.location_id=location.id
       WHERE p.id=%s AND NOT EXISTS(SELECT 1 FROM auto_public_booking_offer_revoke x
                                      WHERE x.offer_id=p.id)
    """+(" FOR SHARE OF p,org,location,bay,service" if lock else ""),(ident,)).fetchone()
    if row is None:
        raise PublicBookingDenied("UNAVAILABLE")
    return row


def _materials(conn,offer_id):
    rows=conn.execute("""
      SELECT sku_id,item_kind,quantity FROM auto_public_booking_material
      WHERE offer_id=%s ORDER BY sku_id
    """,(offer_id,)).fetchall()
    return tuple((sku,kind,int(quantity)) for sku,kind,quantity in rows)


def _safe_offer(row,materials_ready=True):
    return {"id":str(row[0]),"organizationId":str(row[1]),"locationId":str(row[2]),
            "bayId":str(row[3]),"serviceId":str(row[4]),"policyVersion":row[6],
            "serviceTitle":row[7],"durationMinutes":row[8],"bayCode":row[9],
            "timeZone":row[10],"materialsReady":materials_ready}


def _ready(conn,offer_id,materials=None):
    materials=_materials(conn,offer_id) if materials is None else materials
    if not materials:
        return True
    rows=conn.execute("""
       SELECT sku_id,on_hand-reserved FROM auto_stock_balance
       WHERE sku_id=ANY(%s::uuid[])
    """,([m[0] for m in materials],)).fetchall()
    available=dict(rows)
    return all(available.get(sku,0)>=qty for sku,kind,qty in materials)


def list_offers(conn,org,loc):
    _id(org,loc)
    with conn.transaction():
        rows=conn.execute("""
          SELECT id FROM auto_public_booking_offer
          WHERE organization_id=%s AND location_id=%s ORDER BY created_at,id LIMIT 51
        """,(org,loc)).fetchall()
        if len(rows)>50:
            raise PublicBookingConflict("OFFER_LIMIT")
        items=[]
        for (ident,) in rows:
            try:
                row=_offer(conn,ident)
            except PublicBookingDenied:
                continue
            items.append(_safe_offer(row,_ready(conn,ident)))
        return {"syntheticOnly":True,"publicProduction":False,"guaranteed":False,
                "capturePII":False,"items":items}


def publish_offer(conn,token,org,loc,*,bay,service,materials,policy,key):
    _id(org,loc,bay,service,key)
    if (type(policy) is not str or len(policy)!=16 or
        not policy.startswith("DEV-CONSENT-") or not policy[-4:].isdigit() or
        type(materials) is not tuple or len(materials)>8 or
        any(type(x) is not tuple or len(x)!=3 or type(x[0]) is not UUID or
            x[1] not in ("PART","CONSUMABLE") or type(x[2]) is not int or
            not 1<=x[2]<=1_000_000_000 for x in materials) or
        len({m[0] for m in materials})!=len(materials)):
        raise PublicBookingInvalid("INVALID_INPUT")
    with conn.transaction():
        actor=_staff(conn,token,org,loc,("MANAGER",)+(("WAREHOUSE",) if materials else ()))
        expected=tuple(sorted(materials,key=lambda x:str(x[0])))
        prior=conn.execute("""
           SELECT id,organization_id,location_id,bay_id,service_id,actor_id,policy_version
           FROM auto_public_booking_offer WHERE request_key=%s
        """,(key,)).fetchone()
        if prior:
            if prior[1:]!=(org,loc,bay,service,actor,policy) or _materials(conn,prior[0])!=expected:
                raise PublicBookingConflict("KEY_REUSED")
            return {"replayed":True,**_safe_offer(_offer(conn,prior[0]))}
        conn.execute("SELECT id FROM auto_organization WHERE id=%s FOR SHARE",(org,))
        try:
            with conn.transaction():
                ident=uuid4()
                conn.execute("""INSERT INTO auto_public_booking_offer
                  (id,organization_id,location_id,bay_id,service_id,actor_id,
                   policy_version,request_key) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                """,(ident,org,loc,bay,service,actor,policy,key))
                for sku,kind,qty in expected:
                    conn.execute("""INSERT INTO auto_public_booking_material
                      (offer_id,sku_id,item_kind,quantity,actor_id)
                      VALUES (%s,%s,%s,%s,%s)""",(ident,sku,kind,qty,actor))
        except (psycopg.errors.RaiseException,psycopg.errors.UniqueViolation,
                psycopg.errors.CheckViolation,psycopg.errors.ForeignKeyViolation):
            raise PublicBookingConflict("OFFER_CONFLICT") from None
        return {"replayed":False,**_safe_offer(_offer(conn,ident),_ready(conn,ident))}


def revoke_offer(conn,token,org,loc,offer_id,key):
    """Immutable MANAGER withdrawal. Never edits existing requests/receipts."""
    _id(org,loc,offer_id,key)
    with conn.transaction():
        # Lock in the same order as anonymous submit: offer advisory first,
        # then live MANAGER scope, then offer row. Avoid org SHARE -> UPDATE
        # upgrade deadlocks against the anonymous organization quota lock.
        conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,9099))",
                     (str(offer_id),))
        actor=_staff(conn,token,org,loc,("MANAGER",))
        row=conn.execute("""
            SELECT organization_id,location_id FROM auto_public_booking_offer
            WHERE id=%s FOR UPDATE
        """,(offer_id,)).fetchone()
        if row is None or row!=(org,loc):
            raise PublicBookingDenied("FORBIDDEN")
        prior=conn.execute("""
          SELECT actor_id,request_key FROM auto_public_booking_offer_revoke
          WHERE offer_id=%s
        """,(offer_id,)).fetchone()
        if prior:
            if prior!=(actor,key):
                raise PublicBookingConflict("OFFER_ALREADY_WITHDRAWN")
            return {"syntheticOnly":True,"revoked":True,"replayed":True}
        try:
            with conn.transaction():
                conn.execute("""INSERT INTO auto_public_booking_offer_revoke
                    (offer_id,actor_id,request_key) VALUES(%s,%s,%s)
                """,(offer_id,actor,key))
        except (psycopg.errors.RaiseException,psycopg.errors.UniqueViolation):
            raise PublicBookingConflict("OFFER_REVOKE_CONFLICT") from None
        return {"syntheticOnly":True,"revoked":True,"replayed":False}


def _local_day(text):
    if type(text) is not str or len(text)!=10:
        raise PublicBookingInvalid("INVALID_DAY")
    try:
        d=date.fromisoformat(text)
        if d.isoformat()!=text:
            raise ValueError("noncanonical")
        return d
    except ValueError:
        raise PublicBookingInvalid("INVALID_DAY") from None


def slot_candidates(conn,offer_id,local_day):
    d=_local_day(local_day)
    with conn.transaction():
        p=_offer(conn,offer_id)
        try:
            zone=ZoneInfo(p[10])
        except ZoneInfoNotFoundError:
            raise PublicBookingConflict("UNAVAILABLE") from None
        start=datetime(d.year,d.month,d.day,tzinfo=zone).astimezone(timezone.utc)
        nxt=d+timedelta(days=1)
        end=datetime(nxt.year,nxt.month,nxt.day,tzinfo=zone).astimezone(timezone.utc)
        if d < datetime.now(zone).date() or d > (datetime.now(zone)+timedelta(days=60)).date():
            raise PublicBookingInvalid("DAY_OUT_OF_RANGE")
        duration=p[8]
        rows=conn.execute("""
          SELECT step AS starts_at,step+(%s*interval '1 minute') AS ends_at
          FROM generate_series(%s::timestamptz,%s::timestamptz-
                  (%s*interval '1 minute'),interval '30 minutes') step
          WHERE step>clock_timestamp()
            AND NOT EXISTS(SELECT 1 FROM auto_bay_booking b
                 WHERE b.bay_id=%s AND b.canceled_at IS NULL
                   AND b.starts_at<step+(%s*interval '1 minute') AND b.ends_at>step)
            AND NOT EXISTS(SELECT 1 FROM auto_location_opening_policy policy
                 WHERE policy.organization_id=%s AND policy.location_id=%s
                   AND NOT auto_location_hours_allow(
                       %s,policy.windows,policy.closed_dates,
                       step,step+(%s*interval '1 minute')))
          ORDER BY step LIMIT 51
        """,(duration,start,end,duration,p[3],duration,p[1],p[2],p[10],duration)).fetchall()
        return {"syntheticOnly":True,"guaranteed":False,
                "materialsReady":_ready(conn,offer_id),
                "timeZone":p[10],
                "items":[{"startsAt":a.isoformat(),"endsAt":b.isoformat()}
                         for a,b in rows[:50]]}


def _limit(conn,org,offer_id,table,limit):
    # Lock organization row for atomic organization-wide anonymous burst caps.
    row=conn.execute("""SELECT id FROM auto_organization
      WHERE id=%s AND disabled_at IS NULL FOR UPDATE""",(org,)).fetchone()
    if row is None:
        raise PublicBookingDenied("UNAVAILABLE")
    if table=="challenge":
        count=conn.execute("""
          SELECT count(*) FROM auto_public_booking_challenge c
          JOIN auto_public_booking_offer p ON p.id=c.offer_id
          WHERE p.organization_id=%s AND c.created_at>clock_timestamp()-interval '10 minutes'
        """,(org,)).fetchone()[0]
    else:
        count=conn.execute("""
          SELECT count(*) FROM auto_public_booking_request req
          JOIN auto_public_booking_offer p ON p.id=req.offer_id
          WHERE p.organization_id=%s AND req.created_at>clock_timestamp()-interval '10 minutes'
        """,(org,)).fetchone()[0]
    if count>=limit:
        raise PublicBookingConflict("RATE_LIMITED")


def challenge(conn,offer_id):
    with conn.transaction():
        p=_offer(conn,offer_id)
        _limit(conn,p[1],offer_id,"challenge",100)
        left,right=randbelow(8)+2,randbelow(8)+2
        ident=uuid4()
        digest=sha256((str(ident)+":"+str(left+right)).encode()).hexdigest()
        conn.execute("""INSERT INTO auto_public_booking_challenge
          (id,offer_id,answer_digest,expires_at)
          VALUES (%s,%s,%s,clock_timestamp()+interval '5 minutes')""",
                     (ident,offer_id,digest))
        return {"syntheticOnly":True,"challengeId":str(ident),
                "question":f"{left} + {right} = ?",
                "expiresInSeconds":300,"captchaProvider":"DEV_ARITHMETIC_ONLY"}


def _match_slot(conn,p,start,end):
    if (type(start) is not datetime or type(end) is not datetime or
        start.tzinfo is None or end.tzinfo is None or
        start.utcoffset() is None or end.utcoffset() is None or
        end-start!=timedelta(minutes=p[8]) or start<=datetime.now(timezone.utc) or
        start>datetime.now(timezone.utc)+timedelta(days=60)):
        raise PublicBookingInvalid("INVALID_SLOT")
    # Candidate slots are aligned from branch-local midnight, not UTC.
    try:
        local=start.astimezone(ZoneInfo(p[10]))
    except ZoneInfoNotFoundError:
        raise PublicBookingConflict("UNAVAILABLE") from None
    if local.second or local.microsecond or local.minute%30:
        raise PublicBookingInvalid("INVALID_SLOT")
    row=conn.execute("""
       SELECT 1 FROM auto_bay_booking b
       WHERE b.bay_id=%s AND b.canceled_at IS NULL
         AND b.starts_at<%s AND b.ends_at>%s LIMIT 1
    """,(p[3],end,start)).fetchone()
    if row:
        raise PublicBookingConflict("SLOT_TAKEN")
    if conn.execute("""
      SELECT 1 FROM auto_location_opening_policy policy
      WHERE policy.organization_id=%s AND policy.location_id=%s
        AND NOT auto_location_hours_allow(
          %s,policy.windows,policy.closed_dates,%s,%s)
    """,(p[1],p[2],p[10],start,end)).fetchone():
        raise PublicBookingConflict("LOCATION_CLOSED")


def submit_request(conn,offer_id,*,request_key,challenge_id,answer,
                   start,end,processing,marketing):
    _id(offer_id,request_key,challenge_id)
    if (type(answer) is not int or not 0<=answer<=99 or
        type(processing) is not bool or not processing or
        type(marketing) is not bool):
        raise PublicBookingInvalid("INVALID_INPUT")
    with conn.transaction():
        # Serialize an anonymous submission with explicit MANAGER publication
        # withdrawal. No request can commit after the manager revokes the offer.
        conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,9099))",
                     (str(offer_id),))
        p=_offer(conn,offer_id)
        used=conn.execute("""
           SELECT id,offer_id,challenge_id,starts_at,ends_at,
                  processing_consent,marketing_consent
           FROM auto_public_booking_request WHERE request_key=%s
        """,(request_key,)).fetchone()
        expected=(offer_id,challenge_id,start,end,processing,marketing)
        if used:
            if used[1:]!=expected:
                raise PublicBookingConflict("KEY_REUSED")
            return {"requestId":str(used[0]),"receiptKey":str(request_key),
                    "replayed":True,"status":"AWAITING_VERIFICATION",
                    "syntheticOnly":True,"noSlotHold":True}
        # A lost-response retry is not a NEW request and must bypass the
        # already exhausted quota. _limit still serializes all NEW inserts.
        _limit(conn,p[1],offer_id,"request",25)
        _match_slot(conn,p,start,end)
        challenge_row=conn.execute("""
          SELECT answer_digest,expires_at,used_at,offer_id
          FROM auto_public_booking_challenge WHERE id=%s FOR UPDATE
        """,(challenge_id,)).fetchone()
        expected_digest=sha256((str(challenge_id)+":"+str(answer)).encode()).hexdigest()
        if (challenge_row is None or challenge_row[2] is not None or
            challenge_row[3]!=offer_id or
            challenge_row[1]<=datetime.now(timezone.utc) or
            challenge_row[0]!=expected_digest):
            raise PublicBookingConflict("CHALLENGE_INVALID")
        try:
            with conn.transaction():
                ident=uuid4()
                conn.execute("""INSERT INTO auto_public_booking_request
                  (id,offer_id,request_key,challenge_id,starts_at,ends_at,
                   processing_consent,marketing_consent)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                """,(ident,offer_id,request_key,challenge_id,start,end,processing,marketing))
                conn.execute("""UPDATE auto_public_booking_challenge SET used_at=clock_timestamp()
                  WHERE id=%s AND used_at IS NULL""",(challenge_id,))
        except (psycopg.errors.RaiseException,psycopg.errors.UniqueViolation,
                psycopg.errors.CheckViolation,psycopg.errors.ForeignKeyViolation):
            raise PublicBookingConflict("REQUEST_CONFLICT") from None
        return {"requestId":str(ident),"receiptKey":str(request_key),
                "replayed":False,"status":"AWAITING_VERIFICATION",
                "syntheticOnly":True,"noSlotHold":True}


def _request(conn,ident,receipt,*,lock=False):
    _id(ident,receipt)
    row=conn.execute("""
      SELECT req.id,req.offer_id,req.starts_at,req.ends_at,req.marketing_consent,
             req.request_key,req.created_at
      FROM auto_public_booking_request req WHERE req.id=%s
    """+(" FOR UPDATE" if lock else ""),(ident,)).fetchone()
    if row is None or row[5]!=receipt:
        raise PublicBookingDenied("FORBIDDEN")
    return row


def _status(conn,req):
    claim=conn.execute("SELECT 1 FROM auto_public_booking_claim WHERE request_id=%s",(req[0],)).fetchone()
    filled=conn.execute("""SELECT appointment_id,work_order_id FROM auto_public_booking_fulfillment
                           WHERE request_id=%s""",(req[0],)).fetchone()
    withdrew=conn.execute("""SELECT 1 FROM auto_public_booking_consent_withdrawal
                             WHERE request_id=%s""",(req[0],)).fetchone()
    revoked=conn.execute("""SELECT 1 FROM auto_public_booking_offer_revoke
                            WHERE offer_id=%s""",(req[1],)).fetchone()
    return {"syntheticOnly":True,"realDelivery":False,"noSlotHold":filled is None,
            "requestId":str(req[0]),"status":"BOOKED" if filled else
                 "OFFER_WITHDRAWN" if revoked else
                 "WAITING_FOR_STAFF" if claim else "AWAITING_VERIFICATION",
            "marketingEnabled":bool(req[4]) and not bool(withdrew),
            "appointmentId":str(filled[0]) if filled else None,
            "workOrderId":str(filled[1]) if filled else None}


def status(conn,ident,receipt):
    with conn.transaction():
        req=_request(conn,ident,receipt)
        return _status(conn,req)


def claim_request(conn,token,ident,receipt,link,key,processing):
    _id(ident,receipt,link,key)
    if processing is not True:
        raise PublicBookingInvalid("PROCESSING_CONSENT_REQUIRED")
    with conn.transaction():
        req=_request(conn,ident,receipt,lock=True)
        p=_offer(conn,req[1],lock=True)
        try:
            actor=_customer_scope(conn,token,p[1],p[2],link)
        except AppointmentDenied:
            raise PublicBookingDenied("FORBIDDEN") from None
        existing=conn.execute("""
          SELECT actor_id,customer_link_id,request_key
          FROM auto_public_booking_claim WHERE request_id=%s
        """,(ident,)).fetchone()
        if existing:
            if existing!=(actor,link,key):
                raise PublicBookingConflict("ALREADY_CLAIMED")
            return {"replayed":True,**_status(conn,req)}
        if req[6]<datetime.now(timezone.utc)-timedelta(hours=24):
            raise PublicBookingConflict("REQUEST_EXPIRED")
        try:
            with conn.transaction():
                conn.execute("""INSERT INTO auto_public_booking_claim
                   (request_id,actor_id,customer_link_id,request_key,processing_consent)
                   VALUES (%s,%s,%s,%s,true)""",(ident,actor,link,key))
        except (psycopg.errors.RaiseException,psycopg.errors.UniqueViolation):
            raise PublicBookingConflict("CLAIM_CONFLICT") from None
        return {"replayed":False,**_status(conn,req)}


def withdraw_marketing(conn,token,ident,receipt,key):
    _id(ident,receipt,key)
    with conn.transaction():
        req=_request(conn,ident,receipt,lock=True)
        claim=conn.execute("""SELECT actor_id,customer_link_id
           FROM auto_public_booking_claim WHERE request_id=%s""",(ident,)).fetchone()
        if claim is None:
            raise PublicBookingDenied("FORBIDDEN")
        p=_offer(conn,req[1])
        try:
            actor=_customer_scope(conn,token,p[1],p[2],claim[1])
        except AppointmentDenied:
            raise PublicBookingDenied("FORBIDDEN") from None
        if actor!=claim[0]:
            raise PublicBookingDenied("FORBIDDEN")
        prev=conn.execute("""SELECT actor_id,request_key FROM
          auto_public_booking_consent_withdrawal WHERE request_id=%s""",(ident,)).fetchone()
        if prev:
            if prev!=(actor,key):
                raise PublicBookingConflict("WITHDRAW_ALREADY_RECORDED")
            return {"replayed":True,**_status(conn,req)}
        try:
            with conn.transaction():
                conn.execute("""INSERT INTO auto_public_booking_consent_withdrawal
                  (request_id,actor_id,request_key) VALUES (%s,%s,%s)""",(ident,actor,key))
        except (psycopg.errors.UniqueViolation,psycopg.errors.RaiseException):
            raise PublicBookingConflict("WITHDRAW_CONFLICT") from None
        return {"replayed":False,**_status(conn,req)}


def staff_queue(conn,token,org,loc,*,page=0):
    _id(org,loc)
    if type(page) is not int or not 0<=page<=100:
        raise PublicBookingInvalid("INVALID_PAGE")
    with conn.transaction():
        _staff(conn,token,org,loc,("INTAKE",))
        rows=conn.execute("""
          SELECT r.id,r.offer_id,r.starts_at,r.ends_at,r.created_at,
            c.request_id,f.appointment_id,
            EXISTS(SELECT 1 FROM auto_public_booking_offer_revoke revoke
                   WHERE revoke.offer_id=r.offer_id)
          FROM auto_public_booking_request r
          JOIN auto_public_booking_offer o ON o.id=r.offer_id
          LEFT JOIN auto_public_booking_claim c ON c.request_id=r.id
          LEFT JOIN auto_public_booking_fulfillment f ON f.request_id=r.id
          WHERE o.organization_id=%s AND o.location_id=%s
          ORDER BY CASE
             WHEN c.request_id IS NOT NULL AND f.appointment_id IS NULL
               AND NOT EXISTS(SELECT 1 FROM auto_public_booking_offer_revoke rev
                              WHERE rev.offer_id=r.offer_id) THEN 0
             WHEN f.appointment_id IS NULL AND NOT EXISTS(
               SELECT 1 FROM auto_public_booking_offer_revoke rev
               WHERE rev.offer_id=r.offer_id) THEN 1
             ELSE 2 END,
             r.created_at DESC,r.id DESC LIMIT 51 OFFSET %s
        """,(org,loc,page*50)).fetchall()
        has_more=len(rows)>50
        rows=rows[:50]
        return {"syntheticOnly":True,"page":page,"hasMore":has_more,"items":[{
          "requestId":str(req),"offerId":str(offer),"startsAt":start.isoformat(),
          "endsAt":end.isoformat(),"createdAt":created.isoformat(),
          "status":"BOOKED" if appointment else "OFFER_WITHDRAWN" if revoked else
              "WAITING_FOR_STAFF" if claimed else "AWAITING_VERIFICATION",
          "appointmentId":str(appointment) if appointment else None,
          "materials":[{"skuId":str(sku),"kind":kind,"quantity":qty}
              for sku,kind,qty in _materials(conn,offer)]
        } for req,offer,start,end,created,claimed,appointment,revoked in rows]}


def fulfill_request(conn,token,org,loc,ident,key):
    _id(org,loc,ident,key)
    with conn.transaction():
        # Only INTAKE can book. The existing booking service also checks
        # WAREHOUSE for non-empty material requirements.
        actor=_staff(conn,token,org,loc,("INTAKE",))
        row=conn.execute("""
          SELECT r.id,r.offer_id,r.starts_at,r.ends_at
          FROM auto_public_booking_request r
          JOIN auto_public_booking_offer o ON o.id=r.offer_id
          WHERE r.id=%s AND o.organization_id=%s AND o.location_id=%s
          FOR UPDATE OF r
        """,(ident,org,loc)).fetchone()
        if row is None:
            raise PublicBookingDenied("FORBIDDEN")
        p=_offer(conn,row[1],lock=True)
        materials=_materials(conn,row[1])
        if materials:
            _staff(conn,token,org,loc,("INTAKE","WAREHOUSE"))
        claimed=conn.execute("""
          SELECT c.actor_id,c.customer_link_id FROM auto_public_booking_claim c
          WHERE c.request_id=%s FOR SHARE
        """,(ident,)).fetchone()
        if not claimed:
            raise PublicBookingConflict("CLIENT_VERIFICATION_REQUIRED")
        live=conn.execute("""
           SELECT 1 FROM auto_vehicle_customer_link proof
           JOIN auto_vehicle_location_binding binding
              ON binding.vehicle_id=proof.vehicle_id AND binding.location_id=%s
           JOIN account_customer_link acl
              ON acl.customer_id=proof.customer_id AND acl.user_id=%s
              AND acl.revoked_at IS NULL
           JOIN app_user u ON u.id=%s AND u.status='ACTIVE'
           JOIN user_role role ON role.user_id=%s AND role.role_code='CLIENT'
              AND role.revoked_at IS NULL
              AND (role.expires_at IS NULL OR role.expires_at>now())
           WHERE proof.id=%s AND proof.verified_at IS NOT NULL
             AND proof.verified_by IS NOT NULL AND proof.revoked_at IS NULL
           FOR SHARE OF proof,binding,acl,u,role
        """,(loc,claimed[0],claimed[0],claimed[0],claimed[1])).fetchone()
        if not live:
            raise PublicBookingDenied("FORBIDDEN")
        prior=conn.execute("""
          SELECT f.appointment_id,f.work_order_id,f.actor_id,f.request_key
          FROM auto_public_booking_fulfillment f WHERE f.request_id=%s
        """,(ident,)).fetchone()
        if prior:
            if prior[2:]!=(actor,key):
                raise PublicBookingConflict("ALREADY_FULFILLED")
            return {"replayed":True,"syntheticOnly":True,
                    "appointmentId":str(prior[0]),"workOrderId":str(prior[1]),
                    "releaseAllowed":False}
        if row[2]<=datetime.now(timezone.utc):
            raise PublicBookingConflict("SLOT_EXPIRED")
        try:
            appointment,booking,order=create_appointment(
                conn,token,org,loc,p[3],claimed[1],row[2],row[3],
                request_key=uuid5(NAMESPACE_URL,"public-booking:"+str(ident)),
                service_ids=(p[4],),material_items=materials)
            lead=conn.execute("""
               SELECT id FROM auto_crm_lead
               WHERE organization_id=%s AND location_id=%s
                 AND customer_link_id=%s AND source_code='WEB'
               FOR SHARE
            """,(org,loc,claimed[1])).fetchone()
            if lead is None:
                lead_id=uuid4()
                conn.execute("""
                  INSERT INTO auto_crm_lead
                   (id,organization_id,location_id,customer_link_id,owner_id,
                    source_code,request_key)
                  VALUES (%s,%s,%s,%s,%s,'WEB',%s)
                """,(lead_id,org,loc,claimed[1],actor,
                     uuid5(NAMESPACE_URL,"public-crm:"+str(ident))))
                conn.execute("""INSERT INTO auto_crm_lead_stage
                  (id,lead_id,actor_id,stage_code,request_key)
                  VALUES (%s,%s,%s,'QUALIFIED',%s)
                """,(uuid4(),lead_id,actor,
                     uuid5(NAMESPACE_URL,"public-crm-qualified:"+str(ident))))
                conn.execute("""INSERT INTO auto_crm_lead_stage
                  (id,lead_id,actor_id,stage_code,appointment_id,request_key)
                  VALUES (%s,%s,%s,'BOOKED',%s,%s)
                """,(uuid4(),lead_id,actor,appointment,
                     uuid5(NAMESPACE_URL,"public-crm-booked:"+str(ident))))
            else:
                lead_id=lead[0]
            conn.execute("""INSERT INTO auto_public_booking_fulfillment
              (request_id,appointment_id,work_order_id,crm_lead_id,actor_id,request_key)
              VALUES (%s,%s,%s,%s,%s,%s)
            """,(ident,appointment,order,lead_id,actor,key))
        except (AppointmentDenied,):
            raise PublicBookingDenied("FORBIDDEN") from None
        except (AppointmentConflict,AppointmentStockUnavailable,
                AppointmentKeyConflict,AppointmentDurationInvalid,
                AppointmentHoursClosed,psycopg.errors.RaiseException,
                psycopg.errors.UniqueViolation,psycopg.errors.ExclusionViolation,
                psycopg.errors.ForeignKeyViolation,psycopg.errors.DeadlockDetected):
            raise PublicBookingConflict("FULFILL_CONFLICT") from None
        return {"replayed":False,"syntheticOnly":True,"appointmentId":str(appointment),
                "workOrderId":str(order),"crmLeadId":str(lead_id),
                "releaseAllowed":False}
