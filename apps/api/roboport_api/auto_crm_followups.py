"""Synthetic branch-scoped CRM tasks: due date, owner and immutable terminal audit.

No personal contact details, scheduler, messaging or external notifications.
"""
from datetime import date,timedelta,datetime,timezone
from uuid import UUID,uuid4
from zoneinfo import ZoneInfo,ZoneInfoNotFoundError

import psycopg

from .auto_crm_leads import _authority,_lead,LeadDenied,LeadConflict

KINDS=frozenset(("CALLBACK_NOT_SENT","CHECK_DOCUMENTS",
                 "CONFIRM_BOOKING","SERVICE_REVIEW_NOT_SENT"))
ACTIONS=frozenset(("DONE","CANCELED"))
REASONS=frozenset(("NO_LONGER_NEEDED","DUPLICATE","OTHER"))


class FollowupInvalid(ValueError):
    pass


def _safe_day(value):
    if type(value) is not str or len(value)!=10:
        raise FollowupInvalid("INVALID_INPUT")
    try:
        day=date.fromisoformat(value)
        if day.isoformat()!=value:
            raise ValueError
        return day
    except ValueError:
        raise FollowupInvalid("INVALID_INPUT") from None


def _authorize(db,token,org,loc,lead_id):
    if type(lead_id) is not UUID:
        raise FollowupInvalid("INVALID_INPUT")
    actor,manager=_authority(db,token,org,loc)
    lead=_lead(db,org,loc,lead_id,actor,manager)
    return actor,lead


def _task(db,lead_id,task_id):
    row=db.execute(
        """SELECT t.id,t.owner_id,t.actor_id,t.kind,t.due_on,t.created_at,
                  e.action,e.reason_code,e.actor_id,e.created_at
           FROM auto_crm_followup_task t
           LEFT JOIN auto_crm_followup_event e ON e.task_id=t.id
           WHERE t.lead_id=%s AND t.id=%s""",
        (lead_id,task_id),
    ).fetchone()
    if row is None:
        raise LeadDenied("FORBIDDEN")
    return {
        "id":str(row[0]),"ownerId":str(row[1]),"createdById":str(row[2]),
        "kind":row[3],"dueDay":row[4].isoformat(),
        "createdAt":row[5].isoformat(),"status":row[6] or "OPEN",
        "reasonCode":row[7],"closedById":str(row[8]) if row[8] else None,
        "closedAt":row[9].isoformat() if row[9] else None,
    }


def list_followups(db,token,org,loc,lead_id):
    with db.transaction():
        _authorize(db,token,org,loc,lead_id)
        ids=db.execute(
            """SELECT id FROM auto_crm_followup_task
               WHERE lead_id=%s ORDER BY due_on,id LIMIT 50""",
            (lead_id,),
        ).fetchall()
        return {"syntheticOnly":True,"items":[_task(db,lead_id,r[0]) for r in ids]}


def create_followup(db,token,org,loc,lead_id,*,request_key,kind,due_day):
    if type(request_key) is not UUID or kind not in KINDS:
        raise FollowupInvalid("INVALID_INPUT")
    due=_safe_day(due_day)
    with db.transaction():
        actor,lead=_authorize(db,token,org,loc,lead_id)
        location=db.execute(
            """SELECT timezone_name FROM auto_service_location
               WHERE id=%s AND organization_id=%s AND disabled_at IS NULL
               FOR SHARE""",
            (loc,org),
        ).fetchone()
        if location is None:
            raise LeadDenied("FORBIDDEN")
        try:
            zone=ZoneInfo(location[0])
        except (ZoneInfoNotFoundError,ValueError):
            raise LeadDenied("FORBIDDEN") from None
        today=datetime.now(timezone.utc).astimezone(zone).date()
        if not today<=due<=today+timedelta(days=365):
            raise FollowupInvalid("INVALID_INPUT")
        db.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
            (f"auto-followup:{lead_id}:{kind}:{due}",),
        )
        existing=db.execute(
            """SELECT id,lead_id,owner_id,actor_id,kind,due_on,request_key
               FROM auto_crm_followup_task
               WHERE (lead_id=%s AND kind=%s AND due_on=%s)
                  OR request_key=%s FOR UPDATE""",
            (lead_id,kind,due,request_key),
        ).fetchone()
        if existing:
            if existing[1:]!=(lead_id,lead[2],actor,kind,due,request_key):
                raise LeadConflict("KEY_REUSED_OR_DUPLICATE")
            return {"replayed":True,"task":_task(db,lead_id,existing[0])}
        ident=uuid4()
        try:
            with db.transaction():
                db.execute(
                    """INSERT INTO auto_crm_followup_task(
                         id,lead_id,owner_id,actor_id,kind,due_on,request_key
                       ) VALUES(%s,%s,%s,%s,%s,%s,%s)""",
                    (ident,lead_id,lead[2],actor,kind,due,request_key),
                )
        except (psycopg.errors.RaiseException,psycopg.errors.UniqueViolation,
                psycopg.errors.CheckViolation) as exc:
            raise LeadConflict("CONFLICT") from exc
        return {"replayed":False,"task":_task(db,lead_id,ident)}


def finish_followup(db,token,org,loc,lead_id,task_id,*,
                    request_key,action,reason_code):
    if (any(type(x) is not UUID for x in (task_id,request_key))
            or action not in ACTIONS
            or (action=="CANCELED" and reason_code not in REASONS)
            or (action=="DONE" and reason_code is not None)):
        raise FollowupInvalid("INVALID_INPUT")
    with db.transaction():
        actor,lead=_authorize(db,token,org,loc,lead_id)
        # Serializes two employees trying to close the same task.
        row=db.execute(
            """SELECT id FROM auto_crm_followup_task
               WHERE lead_id=%s AND id=%s FOR UPDATE""",
            (lead_id,task_id),
        ).fetchone()
        if row is None:
            raise LeadDenied("FORBIDDEN")
        existing=db.execute(
            """SELECT task_id,actor_id,action,reason_code,request_key
               FROM auto_crm_followup_event
               WHERE task_id=%s OR request_key=%s FOR UPDATE""",
            (task_id,request_key),
        ).fetchone()
        if existing:
            if existing!=(task_id,actor,action,reason_code,request_key):
                raise LeadConflict("ALREADY_CLOSED_OR_KEY_REUSED")
            return {"replayed":True,"task":_task(db,lead_id,task_id)}
        try:
            with db.transaction():
                db.execute(
                    """INSERT INTO auto_crm_followup_event(
                       id,task_id,actor_id,action,reason_code,request_key
                    ) VALUES(%s,%s,%s,%s,%s,%s)""",
                    (uuid4(),task_id,actor,action,reason_code,request_key),
                )
        except (psycopg.errors.RaiseException,psycopg.errors.UniqueViolation,
                psycopg.errors.CheckViolation) as exc:
            raise LeadConflict("CONFLICT") from exc
        return {"replayed":False,"task":_task(db,lead_id,task_id)}
