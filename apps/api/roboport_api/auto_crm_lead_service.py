"""Synthetic-only CRM stage machine; no contacts, messages or outbound traffic."""
from uuid import UUID,uuid4
import psycopg

from .auto_org_authority import organization_role_allows
from .session_lookup import session_subject


class CrmDenied(ValueError):
    pass


class CrmInvalid(ValueError):
    pass


class CrmConflict(ValueError):
    pass


SOURCES=frozenset(("WALK_IN","PHONE_NOT_RECORDED","WEB_FORM_TEST","STAFF_REFERRAL"))
STAGES=frozenset(("NEW","CONTACTED","BOOKED","WON","LOST"))
REASONS=frozenset(("NO_RESPONSE","CUSTOMER_DECLINED","DUPLICATE","OTHER"))
NEXT={"NEW":("CONTACTED","LOST"),"CONTACTED":("BOOKED","LOST"),
      "BOOKED":("WON","LOST"),"WON":(),"LOST":()}


def _authority(db,token,org,loc,*,role="INTAKE"):
    if any(type(x) is not UUID for x in (org,loc)):
        raise CrmDenied("FORBIDDEN")
    if not (organization_role_allows(db,token,org,"INTAKE",loc,for_mutation=True)
            or organization_role_allows(db,token,org,"MANAGER",loc,for_mutation=True)):
        raise CrmDenied("FORBIDDEN")
    actor=session_subject(db,token,for_mutation=True)
    if actor is None:
        raise CrmDenied("FORBIDDEN")
    return actor


def _item(db,org,loc,ident):
    row=db.execute(
        """SELECT l.id,l.owner_id,l.created_by,l.source_code,l.intake_key,
                  l.created_at,b.appointment_id,
                  ev.stage,ev.reason_code,ev.created_at,
                  (SELECT count(*) FROM auto_crm_lead_stage_event history
                   WHERE history.lead_id=l.id)
           FROM auto_crm_lead l
           JOIN LATERAL (
             SELECT stage,reason_code,created_at
             FROM auto_crm_lead_stage_event
             WHERE lead_id=l.id ORDER BY created_at DESC,id DESC LIMIT 1
           ) ev ON TRUE
           LEFT JOIN auto_crm_lead_booking b ON b.lead_id=l.id
           WHERE l.id=%s AND l.organization_id=%s AND l.location_id=%s""",
        (ident,org,loc),
    ).fetchone()
    if row is None:
        raise CrmDenied("FORBIDDEN")
    return {"id":str(row[0]),"ownerId":str(row[1]),"createdById":str(row[2]),
            "sourceCode":row[3],"intakeKey":str(row[4]),
            "createdAt":row[5].isoformat(),
            "appointmentId":str(row[6]) if row[6] else None,
            "stage":row[7],"reasonCode":row[8],
            "stageChangedAt":row[9].isoformat(),"eventCount":row[10]}


def create_lead(db,token,org,loc,*,owner_id,source_code,intake_key,request_key):
    if (any(type(x) is not UUID for x in (owner_id,intake_key,request_key))
            or source_code not in SOURCES):
        raise CrmInvalid("INVALID_INPUT")
    with db.transaction():
        actor=_authority(db,token,org,loc)
        # A transactional key lock serializes parallel retries and prevents
        # a concurrent duplicate from becoming a second CRM case.
        db.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                   (f"auto-crm:{org}:{loc}:{intake_key}",))
        # The same intake key represents one inbound CRM case. Distinct
        # request keys cannot silently create a second case for that source.
        existing=db.execute(
            """SELECT id,request_key,created_by,owner_id,source_code,intake_key
               FROM auto_crm_lead
               WHERE (organization_id=%s AND location_id=%s AND intake_key=%s)
                  OR request_key=%s FOR UPDATE""",
            (org,loc,intake_key,request_key),
        ).fetchone()
        if existing:
            if existing[1:]!=(request_key,actor,owner_id,source_code,intake_key):
                raise CrmConflict("KEY_REUSED_OR_DUPLICATE_INTAKE")
            return {"replayed":True,"lead":_item(db,org,loc,existing[0])}
        if not (organization_role_allows(db,token,org,"MANAGER",loc,for_mutation=True)
                or organization_role_allows(db,token,org,"INTAKE",loc,for_mutation=True)):
            raise CrmDenied("FORBIDDEN")
        # DB trigger independently enforces live scoped MANAGER/INTAKE for owner.
        identifier=uuid4()
        try:
            with db.transaction():
                db.execute(
                    """INSERT INTO auto_crm_lead(
                      id,organization_id,location_id,owner_id,created_by,
                      source_code,intake_key,request_key
                    ) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (identifier,org,loc,owner_id,actor,source_code,intake_key,request_key),
                )
                db.execute(
                    """INSERT INTO auto_crm_lead_stage_event(
                      id,lead_id,actor_id,request_key,stage
                    ) VALUES(%s,%s,%s,%s,'NEW')""",
                    (uuid4(),identifier,actor,uuid4()),
                )
        except (psycopg.errors.UniqueViolation,psycopg.errors.RaiseException,
                psycopg.errors.CheckViolation) as exc:
            raise CrmConflict("CONFLICT") from exc
        return {"replayed":False,"lead":_item(db,org,loc,identifier)}


def list_leads(db,token,org,loc):
    with db.transaction():
        _authority(db,token,org,loc)
        ids=db.execute(
            """SELECT id FROM auto_crm_lead
               WHERE organization_id=%s AND location_id=%s
               ORDER BY created_at DESC,id DESC LIMIT 50""",
            (org,loc),
        ).fetchall()
        return {"syntheticOnly":True,"items":[_item(db,org,loc,x[0]) for x in ids]}


def _lead_for_change(db,org,loc,ident):
    row=db.execute(
        """SELECT owner_id FROM auto_crm_lead
           WHERE id=%s AND organization_id=%s AND location_id=%s FOR UPDATE""",
        (ident,org,loc),
    ).fetchone()
    if row is None:
        raise CrmDenied("FORBIDDEN")
    return row[0]


def link_appointment(db,token,org,loc,ident,*,appointment_id,request_key):
    if any(type(x) is not UUID for x in (ident,appointment_id,request_key)):
        raise CrmInvalid("INVALID_INPUT")
    with db.transaction():
        actor=_authority(db,token,org,loc)
        owner=_lead_for_change(db,org,loc,ident)
        if actor!=owner and not organization_role_allows(
            db,token,org,"MANAGER",loc,for_mutation=True,
        ):
            raise CrmDenied("FORBIDDEN")
        current=_item(db,org,loc,ident)
        if current["stage"] not in ("NEW","CONTACTED"):
            raise CrmConflict("STAGE_CLOSED")
        row=db.execute(
            """SELECT appointment_id,actor_id,request_key
               FROM auto_crm_lead_booking WHERE lead_id=%s FOR UPDATE""",
            (ident,),
        ).fetchone()
        if row:
            if row!=(appointment_id,actor,request_key):
                raise CrmConflict("BOOKING_ALREADY_LINKED")
            return {"replayed":True,"lead":current}
        try:
            with db.transaction():
                db.execute(
                    """INSERT INTO auto_crm_lead_booking(
                       lead_id,appointment_id,actor_id,request_key
                    ) VALUES(%s,%s,%s,%s)""",
                    (ident,appointment_id,actor,request_key),
                )
        except (psycopg.errors.RaiseException,psycopg.errors.UniqueViolation,
                psycopg.errors.CheckViolation) as exc:
            raise CrmConflict("BOOKING_INVALID_OR_ALREADY_LINKED") from exc
        return {"replayed":False,"lead":_item(db,org,loc,ident)}


def transition_lead(db,token,org,loc,ident,*,stage,reason_code,request_key):
    if (type(ident) is not UUID or type(request_key) is not UUID
            or stage not in STAGES
            or (stage=="LOST" and reason_code not in REASONS)
            or (stage!="LOST" and reason_code is not None)):
        raise CrmInvalid("INVALID_INPUT")
    with db.transaction():
        actor=_authority(db,token,org,loc)
        owner=_lead_for_change(db,org,loc,ident)
        if actor!=owner and not organization_role_allows(
            db,token,org,"MANAGER",loc,for_mutation=True,
        ):
            raise CrmDenied("FORBIDDEN")
        original=db.execute(
            """SELECT lead_id,actor_id,stage,reason_code
               FROM auto_crm_lead_stage_event WHERE request_key=%s""",
            (request_key,),
        ).fetchone()
        if original:
            if original!=(ident,actor,stage,reason_code):
                raise CrmConflict("KEY_REUSED")
            return {"replayed":True,"lead":_item(db,org,loc,ident)}
        current=_item(db,org,loc,ident)
        if stage not in NEXT[current["stage"]]:
            raise CrmConflict("ILLEGAL_TRANSITION")
        if stage in ("BOOKED","WON") and current["appointmentId"] is None:
            raise CrmConflict("APPOINTMENT_REQUIRED")
        try:
            with db.transaction():
                db.execute(
                    """INSERT INTO auto_crm_lead_stage_event(
                       id,lead_id,actor_id,request_key,stage,reason_code
                    ) VALUES(%s,%s,%s,%s,%s,%s)""",
                    (uuid4(),ident,actor,request_key,stage,reason_code),
                )
        except (psycopg.errors.RaiseException,psycopg.errors.UniqueViolation,
                psycopg.errors.CheckViolation) as exc:
            raise CrmConflict("CONFLICT") from exc
        return {"replayed":False,"lead":_item(db,org,loc,ident)}
