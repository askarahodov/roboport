"""Synthetic inbound interaction evidence; no provider or outbound contact."""
from hashlib import sha256
from uuid import UUID,uuid4

import psycopg

from .auto_crm_leads import _authority,_lead,LeadDenied,LeadConflict
from .keyset_cursor import decode_cursor,encode_cursor

CHANNELS=("CALL","EMAIL","SMS","MESSENGER","WALK_IN")
OUTCOMES=("REQUEST_RECEIVED","CALLBACK_REQUIRED","INFORMATION_REQUESTED","DECLINED")
PAGE=25


def _item(row):
    ident,actor,owner,source,channel,outcome,stamp=row
    return {"interactionId":str(ident),"actorId":str(actor),"ownerId":str(owner),
            "sourceCode":source,"channelCode":channel,"outcomeCode":outcome,
            "direction":"INBOUND_SIM","createdAt":stamp.isoformat()}


def _envelope(lead_id):
    return {"syntheticOnly":True,"sendAllowed":False,"leadId":str(lead_id)}


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
          SELECT id,actor_id,owner_id,source_code,channel_code,outcome_code,created_at
          FROM auto_crm_interaction
          WHERE lead_id=%s AND organization_id=%s AND location_id=%s
            AND (%s::timestamptz IS NULL OR (created_at,id)<(%s::timestamptz,%s::uuid))
          ORDER BY created_at DESC,id DESC LIMIT %s
        """,(lead_id,org,loc,*pos,PAGE+1)).fetchall()
        page=rows[:PAGE]
        return {**_envelope(lead_id),"items":[_item(row) for row in page],
                "nextCursor":encode_cursor(page[-1][6],page[-1][0],scope,token)
                    if len(rows)>PAGE else None}


def record_interaction(conn,token,org,loc,lead_id,key,channel,outcome):
    if (any(type(x) is not UUID for x in (org,loc,lead_id,key)) or
        channel not in CHANNELS or outcome not in OUTCOMES):
        raise LeadDenied("INVALID_INPUT")
    with conn.transaction():
        actor,manager=_authority(conn,token,org,loc)
        conn.execute("""
          SELECT id FROM auto_crm_lead WHERE id=%s AND organization_id=%s
            AND location_id=%s FOR UPDATE
        """,(lead_id,org,loc))
        lead=_lead(conn,org,loc,lead_id,actor,manager)
        prior=conn.execute("""
          SELECT lead_id,organization_id,location_id,id,actor_id,owner_id,
                 source_code,channel_code,outcome_code,created_at
          FROM auto_crm_interaction WHERE request_key=%s
        """,(key,)).fetchone()
        if prior:
            if prior[:3]!=(lead_id,org,loc) or prior[4]!=actor or prior[7:9]!=(channel,outcome):
                raise LeadConflict("KEY_REUSED")
            return {**_envelope(lead_id),"replayed":True,"item":_item(prior[3:])}
        try:
            row=conn.execute("""
              INSERT INTO auto_crm_interaction
                (id,lead_id,organization_id,location_id,actor_id,owner_id,
                 source_code,channel_code,outcome_code,request_key)
              VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
              RETURNING id,actor_id,owner_id,source_code,channel_code,outcome_code,created_at
            """,(uuid4(),lead_id,org,loc,actor,lead[2],lead[3],channel,outcome,key)).fetchone()
        except (psycopg.errors.UniqueViolation,psycopg.errors.RaiseException,
                psycopg.errors.CheckViolation,psycopg.errors.ForeignKeyViolation):
            raise LeadConflict("INTERACTION_CONFLICT") from None
        return {**_envelope(lead_id),"replayed":False,"item":_item(row)}
