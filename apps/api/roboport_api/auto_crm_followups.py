"""Synthetic CRM follow-up workflow: own/manager role, immutable task evidence.

No actual calls, messages, timers, consent assertions or customer PII.
"""
from datetime import datetime, timezone
from uuid import UUID, uuid4

import psycopg

from .auto_crm_leads import _authority, _lead, LeadDenied, LeadConflict


TASK_CODES=("CALL_BACK","CHECK_BOOKING","SERVICE_REVIEW")
ACTIONS=("DONE","CANCELED")


def _task(conn,lead_id,task_id):
    row=conn.execute(
        """SELECT t.id,t.owner_id,t.creator_id,t.task_code,t.due_at,
                  t.created_at,e.id,e.actor_id,e.action,e.created_at,t.origin_work_order_id
           FROM auto_crm_followup_task t
           LEFT JOIN auto_crm_followup_event e ON e.task_id=t.id
           WHERE t.id=%s AND t.lead_id=%s FOR SHARE OF t""",
        (task_id,lead_id),
    ).fetchone()
    if row is None:
        raise LeadDenied("FORBIDDEN")
    ident,owner,creator,code,due,created,e_id,e_actor,e_action,e_created,origin=row
    return {
        "taskId":str(ident),"ownerId":str(owner),"creatorId":str(creator),
        "taskCode":code,"dueAt":due.isoformat(),"createdAt":created.isoformat(),
        "status":e_action if e_action else "OPEN",
        "originWorkOrderId":str(origin) if origin else None,
        "event":None if e_id is None else {
            "id":str(e_id),"actorId":str(e_actor),
            "action":e_action,"createdAt":e_created.isoformat(),
        },
    }


def list_followups(conn,token,org,loc,lead_id):
    if type(lead_id) is not UUID:
        raise LeadDenied("INVALID_INPUT")
    with conn.transaction():
        actor,manager=_authority(conn,token,org,loc)
        _lead(conn,org,loc,lead_id,actor,manager)
        rows=conn.execute(
            """SELECT id FROM auto_crm_followup_task
               WHERE lead_id=%s ORDER BY due_at,id LIMIT 101""",
            (lead_id,),
        ).fetchall()
        if len(rows)>100:
            raise LeadConflict("TASK_LIMIT")
        return {"syntheticOnly":True,"items":[_task(conn,lead_id,r[0]) for r in rows]}


def create_followup(conn,token,org,loc,lead_id,key,code,due_at,origin_work_order_id=None):
    if (any(type(x) is not UUID for x in (lead_id,key))
            or code not in TASK_CODES
            or (origin_work_order_id is not None and
                (type(origin_work_order_id) is not UUID or code!='SERVICE_REVIEW'))
            or type(due_at) is not datetime
            or due_at.tzinfo is None or due_at.utcoffset() is None):
        raise LeadDenied("INVALID_INPUT")
    with conn.transaction():
        actor,manager=_authority(conn,token,org,loc)
        # Acquire exclusive lead lock BEFORE the shared proof/lead read:
        # acquiring SHARE first would deadlock two concurrent creators.
        conn.execute(
            """SELECT id FROM auto_crm_lead
               WHERE id=%s AND organization_id=%s AND location_id=%s
               FOR UPDATE""",
            (lead_id,org,loc),
        )
        lead=_lead(conn,org,loc,lead_id,actor,manager)
        existing=conn.execute(
            """SELECT id,lead_id,creator_id,task_code,due_at,origin_work_order_id
               FROM auto_crm_followup_task WHERE request_key=%s""",
            (key,),
        ).fetchone()
        if existing:
            if (existing[1:] != (lead_id,actor,code,due_at,origin_work_order_id)):
                raise LeadConflict("KEY_REUSED")
            return {"replayed":True,**_task(conn,lead_id,existing[0])}
        if conn.execute(
            "SELECT count(*) FROM auto_crm_followup_task WHERE lead_id=%s",
            (lead_id,),
        ).fetchone()[0]>=100:
            raise LeadConflict("TASK_LIMIT")
        ident=uuid4()
        try:
            with conn.transaction():
                conn.execute(
                    """INSERT INTO auto_crm_followup_task
                       (id,lead_id,owner_id,creator_id,task_code,due_at,request_key,origin_work_order_id)
                       VALUES(%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (ident,lead_id,lead[2],actor,code,due_at,key,origin_work_order_id),
                )
        except (psycopg.errors.RaiseException,psycopg.errors.UniqueViolation,
                psycopg.errors.CheckViolation) as exc:
            raise LeadConflict("TASK_CONFLICT") from exc
        return {"replayed":False,**_task(conn,lead_id,ident)}


def resolve_followup(conn,token,org,loc,lead_id,task_id,key,action):
    if (any(type(x) is not UUID for x in (lead_id,task_id,key))
            or action not in ACTIONS):
        raise LeadDenied("INVALID_INPUT")
    with conn.transaction():
        actor,manager=_authority(conn,token,org,loc)
        _lead(conn,org,loc,lead_id,actor,manager)
        # Lock the task to serialize competing DONE/CANCELED from
        # two legitimate staff actors and prevent double terminal events.
        conn.execute(
            """SELECT id FROM auto_crm_followup_task
               WHERE id=%s AND lead_id=%s FOR UPDATE""",
            (task_id,lead_id),
        )
        existing_task=conn.execute(
            "SELECT id FROM auto_crm_followup_task WHERE id=%s AND lead_id=%s",
            (task_id,lead_id),
        ).fetchone()
        if existing_task is None:
            raise LeadDenied("FORBIDDEN")
        existing=conn.execute(
            """SELECT task_id,actor_id,action FROM auto_crm_followup_event
               WHERE request_key=%s""",(key,),
        ).fetchone()
        if existing:
            if existing!=(task_id,actor,action):
                raise LeadConflict("KEY_REUSED")
            return {"replayed":True,**_task(conn,lead_id,task_id)}
        if conn.execute(
            "SELECT 1 FROM auto_crm_followup_event WHERE task_id=%s",
            (task_id,),
        ).fetchone():
            raise LeadConflict("ALREADY_RESOLVED")
        try:
            with conn.transaction():
                conn.execute(
                    """INSERT INTO auto_crm_followup_event
                       (id,task_id,actor_id,action,request_key)
                       VALUES(%s,%s,%s,%s,%s)""",
                    (uuid4(),task_id,actor,action,key),
                )
        except (psycopg.errors.RaiseException,psycopg.errors.UniqueViolation,
                psycopg.errors.CheckViolation) as exc:
            raise LeadConflict("TRANSITION_CONFLICT") from exc
        return {"replayed":False,**_task(conn,lead_id,task_id)}


def create_post_service_review(conn,token,org,loc,lead_id,key,work_order_id,due_at):
    """One staff-planned SERVICE_REVIEW after booked order FINISH and independent QC PASS.

    The DB trigger enforces booking lineage, live proof and QC atomically;
    a unique work_order_id makes competing different-key creates conflict.
    """
    if type(work_order_id) is not UUID:
        raise LeadDenied("INVALID_INPUT")
    return create_followup(
        conn,token,org,loc,lead_id,key,"SERVICE_REVIEW",due_at,work_order_id,
    )
