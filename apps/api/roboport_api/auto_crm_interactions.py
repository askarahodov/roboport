"""Synthetic inbound interaction evidence; no provider or outbound contact."""
from hashlib import sha256
from uuid import UUID,uuid4,uuid5,NAMESPACE_URL
from datetime import datetime,timezone,timedelta

import psycopg

from .auto_crm_leads import _authority,_lead,LeadDenied,LeadConflict
from .keyset_cursor import decode_cursor,encode_cursor
from .auto_crm_followups import create_followup

CHANNELS=("CALL","EMAIL","SMS","MESSENGER","WALK_IN")
OUTCOMES=("REQUEST_RECEIVED","CALLBACK_REQUIRED","INFORMATION_REQUESTED","DECLINED")
PAGE=25


def _item(row):
    ident,actor,owner,source,channel,outcome,stamp,task_id,due=row
    return {"interactionId":str(ident),"actorId":str(actor),"ownerId":str(owner),
            "sourceCode":source,"channelCode":channel,"outcomeCode":outcome,
            "direction":"INBOUND_SIM","createdAt":stamp.isoformat(),
            "callbackTaskId":str(task_id) if task_id else None,
            "callbackDueAt":due.astimezone(timezone.utc).isoformat() if due else None}


def _envelope(lead_id,actor):
    return {"syntheticOnly":True,"sendAllowed":False,"leadId":str(lead_id),
            "viewerId":str(actor)}


def list_interactions(conn,token,org,loc,lead_id,cursor=None):
    if type(lead_id) is not UUID:
        raise LeadDenied("INVALID_INPUT")
    with conn.transaction():
        actor,manager=_authority(conn,token,org,loc)
        _lead(conn,org,loc,lead_id,actor,manager)
        scope=sha256(f"crm-interactions:{org}:{loc}:{lead_id}".encode()).hexdigest()[:32]
        try:
            after=decode_cursor(cursor,scope,token) if cursor is not None else None
        except ValueError:
            raise LeadDenied("INVALID_INPUT") from None
        pos=(after[0],after[0],after[1]) if after else (None,None,None)
        rows=conn.execute("""
          SELECT i.id,i.actor_id,i.owner_id,i.source_code,i.channel_code,i.outcome_code,
                 i.created_at,i.callback_task_id,t.due_at
          FROM auto_crm_interaction i LEFT JOIN auto_crm_followup_task t ON t.id=i.callback_task_id
          WHERE i.lead_id=%s AND i.organization_id=%s AND i.location_id=%s
            AND (%s::timestamptz IS NULL OR (i.created_at,i.id)<(%s::timestamptz,%s::uuid))
          ORDER BY i.created_at DESC,i.id DESC LIMIT %s
        """,(lead_id,org,loc,*pos,PAGE+1)).fetchall()
        page=rows[:PAGE]
        return {**_envelope(lead_id,actor),"items":[_item(row) for row in page],
                "nextCursor":encode_cursor(page[-1][6],page[-1][0],scope,token)
                    if len(rows)>PAGE else None}


def record_interaction(conn,token,org,loc,lead_id,key,channel,outcome,callback_due=None,expected_actor=None):
    if (any(type(x) is not UUID for x in (org,loc,lead_id,key)) or
        channel not in CHANNELS or outcome not in OUTCOMES or
        (expected_actor is not None and type(expected_actor) is not UUID) or
        (callback_due is not None and (outcome!="CALLBACK_REQUIRED" or
          type(callback_due) is not datetime or callback_due.tzinfo is None or
          callback_due.utcoffset() is None))):
        raise LeadDenied("INVALID_INPUT")
    with conn.transaction():
        actor,manager=_authority(conn,token,org,loc)
        if expected_actor is not None and expected_actor!=actor:
            raise LeadDenied("FORBIDDEN")
        conn.execute("""
          SELECT id FROM auto_crm_lead WHERE id=%s AND organization_id=%s
            AND location_id=%s FOR UPDATE
        """,(lead_id,org,loc))
        lead=_lead(conn,org,loc,lead_id,actor,manager)
        prior=conn.execute("""
          SELECT i.lead_id,i.organization_id,i.location_id,i.id,i.actor_id,i.owner_id,
                 i.source_code,i.channel_code,i.outcome_code,i.created_at,i.callback_task_id,t.due_at
          FROM auto_crm_interaction i LEFT JOIN auto_crm_followup_task t ON t.id=i.callback_task_id
          WHERE i.request_key=%s
        """,(key,)).fetchone()
        if prior:
            if prior[:3]!=(lead_id,org,loc) or prior[4]!=actor or prior[7:9]!=(channel,outcome) or prior[11]!=callback_due:
                raise LeadConflict("KEY_REUSED")
            return {**_envelope(lead_id,actor),"replayed":True,"requestKey":str(key),"item":_item(prior[3:])}
        task_id=None
        if callback_due is not None:
            now=datetime.now(timezone.utc)
            if not now<callback_due<=now+timedelta(days=365):
                raise LeadDenied("INVALID_INPUT")
            task=create_followup(conn,token,org,loc,lead_id,
                                 uuid5(NAMESPACE_URL,"crm-interaction-callback:"+str(key)),
                                 "CALL_BACK",callback_due)
            if task["replayed"]:
                raise LeadConflict("CALLBACK_KEY_CONFLICT")
            task_id=UUID(task["taskId"])
        try:
            row=conn.execute("""
              INSERT INTO auto_crm_interaction
                (id,lead_id,organization_id,location_id,actor_id,owner_id,
                 source_code,channel_code,outcome_code,request_key,callback_task_id)
              VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
              RETURNING id,actor_id,owner_id,source_code,channel_code,outcome_code,created_at,callback_task_id
            """,(uuid4(),lead_id,org,loc,actor,lead[2],lead[3],channel,outcome,key,task_id)).fetchone()
        except (psycopg.errors.UniqueViolation,psycopg.errors.RaiseException,
                psycopg.errors.CheckViolation,psycopg.errors.ForeignKeyViolation):
            raise LeadConflict("INTERACTION_CONFLICT") from None
        return {**_envelope(lead_id,actor),"replayed":False,"requestKey":str(key),"item":_item((*row,callback_due.astimezone(timezone.utc) if callback_due else None))}
