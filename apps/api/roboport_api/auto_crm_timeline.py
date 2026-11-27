"""Единая история синтетической сделки; исходные журналы не изменяются."""
from hashlib import sha256
from uuid import UUID

from .auto_crm_leads import _authority, _lead, LeadDenied
from .auto_crm_consent import _lead_customer, _account_users, _snapshot
from .keyset_cursor import encode_cursor, decode_cursor

KINDS=("CREATED","STAGE","ASSIGNED","INBOUND","TASK_CREATED","TASK_RESOLVED","MOCK")
PAGE=25


def _position(cursor,scope,token):
    if cursor is None:
        return (None,None,None,None)
    try:
        if type(cursor) is not str or len(cursor)>256:
            raise ValueError()
        prefix,body=cursor.split(".",1)
        if prefix not in tuple(str(i) for i in range(len(KINDS))):
            raise ValueError()
        stamp,ident=decode_cursor(body,scope+":"+prefix,token)
        return stamp,stamp,int(prefix),ident
    except ValueError:
        raise LeadDenied("INVALID_INPUT") from None


def read_timeline(conn,token,org,loc,lead_id,cursor=None):
    if type(lead_id) is not UUID:
        raise LeadDenied("INVALID_INPUT")
    with conn.transaction():
        actor,manager=_authority(conn,token,org,loc)
        lead=_lead(conn,org,loc,lead_id,actor,manager)
        scope=sha256(f"crm-timeline:{org}:{loc}:{lead_id}".encode()).hexdigest()[:32]
        pos=_position(cursor,scope,token)
        # Use kind as part of the keyset: UUIDs may coincide across independent tables.
        # All rows share the same live lead lock and proof, never infer a link by VIN.
        rows=conn.execute("""
          WITH timeline AS (
            SELECT 0 AS kind,id,created_at,owner_id AS actor,source_code::text AS code,
                   NULL::uuid AS related,NULL::text AS channel,NULL::uuid[] AS consent
            FROM auto_crm_lead WHERE id=%(lead)s
            UNION ALL
            SELECT 1,id,created_at,actor_id,stage_code,NULL,NULL,NULL
            FROM auto_crm_lead_stage WHERE lead_id=%(lead)s
            UNION ALL
            SELECT 2,id,created_at,actor_id,'ASSIGNED',assignee_id,NULL,NULL
            FROM auto_crm_lead_assignment_event WHERE lead_id=%(lead)s
            UNION ALL
            SELECT 3,id,created_at,actor_id,outcome_code,callback_task_id,channel_code,NULL
            FROM auto_crm_interaction WHERE lead_id=%(lead)s
              AND organization_id=%(org)s AND location_id=%(loc)s
            UNION ALL
            SELECT 4,id,created_at,creator_id,task_code,NULL,NULL,NULL
            FROM auto_crm_followup_task WHERE lead_id=%(lead)s
            UNION ALL
            SELECT 5,e.id,e.created_at,e.actor_id,e.action,t.id,NULL,NULL
            FROM auto_crm_followup_event e JOIN auto_crm_followup_task t ON t.id=e.task_id
            WHERE t.lead_id=%(lead)s
            UNION ALL
            SELECT 6,id,created_at,actor_id,template_code,NULL,channel_code,consent_event_ids
            FROM auto_crm_mock_outbox WHERE lead_id=%(lead)s
              AND organization_id=%(org)s AND location_id=%(loc)s
          )
          SELECT kind,id,created_at,actor,code,related,channel,consent
          FROM timeline
          WHERE (%(stamp)s::timestamptz IS NULL OR
                 (created_at,kind,id)<(%(after)s::timestamptz,%(kind)s::int,%(id)s::uuid))
          ORDER BY created_at DESC,kind DESC,id DESC LIMIT 26
        """,{"lead":lead_id,"org":org,"loc":loc,"stamp":pos[0],"after":pos[1],
              "kind":pos[2],"id":pos[3]}).fetchall()
        page=rows[:PAGE]
        flags,snapshots={},{}
        if any(row[0]==6 for row in page):
            customer=_lead_customer(conn,lead)
            # Same account ordering and consent lock as mock creation.
            for user in _account_users(conn,customer):
                conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,10102))",(str(user),))
            flags,snapshots=_snapshot(conn,customer)
        items=[]
        for kind,ident,stamp,actor_id,code,related,channel,consent in page:
            status=None
            if kind==6:
                status="MOCK_ONLY" if flags.get(channel) and snapshots.get(channel)==consent else "SUPPRESSED"
            items.append({"kind":KINDS[kind],"eventId":str(ident),"createdAt":stamp.isoformat(),
                          "actorId":str(actor_id),"code":code,"relatedId":str(related) if related else None,
                          "channelCode":channel,"status":status})
        next_cursor=None
        if len(rows)>PAGE:
            kind,ident,stamp,*_=page[-1]
            prefix=str(kind)
            next_cursor=prefix+"."+encode_cursor(stamp,ident,scope+":"+prefix,token)
        return {"syntheticOnly":True,"sendAllowed":False,"legalConsentVerified":False,
                "leadId":str(lead_id),"items":items,"nextCursor":next_cursor}
