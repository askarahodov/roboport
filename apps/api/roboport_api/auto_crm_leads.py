"""Private synthetic CRM intake: one active lead per verified link/source/branch and immutable repeat cycles.

No phones, message payloads or outbound integrations. Stages are append-only;
DB trigger independently enforces ordering and WorkOrder booking lineage.
"""
from uuid import UUID, uuid4
from datetime import datetime
import base64
import binascii
import psycopg

from .auto_org_authority import organization_role_allows
from .session_lookup import session_subject


class LeadDenied(ValueError):
    pass


class LeadConflict(ValueError):
    pass


def _authority(conn, token,org,loc):
    if any(type(x) is not UUID for x in (org,loc)) or type(token) is not str:
        raise LeadDenied("FORBIDDEN")
    actor=session_subject(conn,token,for_mutation=True)
    if actor is None:
        raise LeadDenied("FORBIDDEN")
    manager=organization_role_allows(conn,token,org,"MANAGER",loc,for_mutation=True)
    intake=organization_role_allows(conn,token,org,"INTAKE",loc,for_mutation=True)
    if not manager and not intake:
        raise LeadDenied("FORBIDDEN")
    return actor,manager


def _proof(conn,org,loc,link):
    return conn.execute(
        """SELECT 1 FROM auto_vehicle_customer_link p
           JOIN auto_vehicle_location_binding binding
             ON binding.vehicle_id=p.vehicle_id AND binding.location_id=%s
           JOIN auto_service_location l ON l.id=binding.location_id
             AND l.organization_id=%s AND l.disabled_at IS NULL
           JOIN auto_organization o ON o.id=l.organization_id AND o.disabled_at IS NULL
           WHERE p.id=%s AND p.verified_at IS NOT NULL
             AND p.verified_by IS NOT NULL AND p.revoked_at IS NULL
           FOR SHARE OF p,l,o""",
        (loc,org,link),
    ).fetchone() is not None


def _lead(conn,org,loc,lead_id,actor,manager):
    conn.execute(
        """SELECT id FROM auto_crm_lead
           WHERE id=%s AND organization_id=%s AND location_id=%s
           FOR SHARE""",
        (lead_id,org,loc),
    )
    # A separate statement after the lead lock sees an assignment committed
    # while this request waited for the row lock.
    row=conn.execute(
        """SELECT lead.id,lead.customer_link_id,
                  COALESCE(assignment.assignee_id,lead.owner_id) AS owner_id,
                  lead.source_code,lead.request_key,lead.created_at,lead.visit_number
           FROM auto_crm_lead lead
           LEFT JOIN LATERAL(
             SELECT assignee_id FROM auto_crm_lead_assignment_event
             WHERE lead_id=lead.id ORDER BY created_at DESC,id DESC LIMIT 1
           ) assignment ON TRUE
           WHERE lead.id=%s AND lead.organization_id=%s AND lead.location_id=%s""",
        (lead_id,org,loc),
    ).fetchone()
    if row is None or (not manager and row[2]!=actor) or not _proof(conn,org,loc,row[1]):
        raise LeadDenied("FORBIDDEN")
    return row


def _state(conn,lead):
    events=conn.execute(
        """SELECT id,actor_id,stage_code,reason_code,appointment_id,request_key,created_at
           FROM auto_crm_lead_stage WHERE lead_id=%s
           ORDER BY created_at,id LIMIT 100""",
        (lead[0],),
    ).fetchall()
    if len(events)>=100:
        raise LeadConflict("EVENT_LIMIT")
    assignments=conn.execute(
        """SELECT id,actor_id,assignee_id,created_at
           FROM auto_crm_lead_assignment_event
           WHERE lead_id=%s ORDER BY created_at,id LIMIT 50""",
        (lead[0],),
    ).fetchall()
    if len(assignments)>=50:
        raise LeadConflict("ASSIGNMENT_EVENT_LIMIT")
    return {
        "leadId":str(lead[0]),"customerLinkId":str(lead[1]),
        "ownerId":str(lead[2]),"sourceCode":lead[3],
        "status":events[-1][2] if events else "NEW",
        "createdAt":lead[5].isoformat(),"syntheticOnly":True,"visitNumber":lead[6],
        "events":[{
            "id":str(i),"actorId":str(actor),"stageCode":stage,
            "reasonCode":reason,"appointmentId":str(appointment) if appointment else None,
            "createdAt":stamp.isoformat(),
        } for i,actor,stage,reason,appointment,key,stamp in events],
        "assignmentEvents":[{
            "id":str(i),"actorId":str(actor),"assigneeId":str(assignee),
            "createdAt":stamp.isoformat(),
        } for i,actor,assignee,stamp in assignments],
    }


def latest_lead_cycle(conn,org,loc,link,source):
    """Internal scoped cycle lookup. No previous lead identity is returned to clients."""
    return conn.execute("""
        SELECT lead.id,lead.visit_number,COALESCE(stage.stage_code,'NEW')
        FROM auto_crm_lead lead
        LEFT JOIN LATERAL (
          SELECT stage_code FROM auto_crm_lead_stage
          WHERE lead_id=lead.id ORDER BY created_at DESC,id DESC LIMIT 1
        ) stage ON TRUE
        WHERE lead.organization_id=%s AND lead.location_id=%s
          AND lead.customer_link_id=%s AND lead.source_code=%s
        ORDER BY lead.visit_number DESC LIMIT 1
    """,(org,loc,link,source)).fetchone()


def create_lead(conn,token,org,loc,link,source,key):
    if (type(link) is not UUID or type(key) is not UUID
            or source not in ("WEB","PHONE","WALK_IN")):
        raise LeadDenied("INVALID_INPUT")
    with conn.transaction():
        actor,manager=_authority(conn,token,org,loc)
        if not _proof(conn,org,loc,link):
            raise LeadDenied("FORBIDDEN")
        # A stable UUID serializes retries, even across concurrent requests.
        conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                     (str(key),))
        used=conn.execute(
            """SELECT id,organization_id,location_id,customer_link_id,
                      owner_id,source_code FROM auto_crm_lead WHERE request_key=%s""",
            (key,),
        ).fetchone()
        if used:
            if used[1:]!=(org,loc,link,actor,source):
                raise LeadConflict("KEY_REUSED")
            lead=_lead(conn,org,loc,used[0],actor,manager)
            return {"replayed":True,**_state(conn,lead)}
        latest=latest_lead_cycle(conn,org,loc,link,source)
        if latest and latest[2] not in ("BOOKED","LOST"):
            raise LeadConflict("ACTIVE_LEAD_EXISTS")
        # Unique scope/visit and predecessor constraints independently reject
        # competing new keys. Terminal stage evidence is never reopened.
        try:
            with conn.transaction():
                ident=uuid4()
                conn.execute(
                    """INSERT INTO auto_crm_lead(
                       id,organization_id,location_id,customer_link_id,
                       owner_id,source_code,request_key,visit_number,previous_lead_id)
                       VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (ident,org,loc,link,actor,source,key,
                     latest[1]+1 if latest else 1,latest[0] if latest else None),
                )
        except (psycopg.errors.UniqueViolation,psycopg.errors.RaiseException,
                psycopg.errors.CheckViolation) as exc:
            raise LeadConflict("DUPLICATE_OR_REVOKED") from exc
        lead=_lead(conn,org,loc,ident,actor,manager)
        return {"replayed":False,**_state(conn,lead)}


def transition_lead(conn,token,org,loc,ident,key,stage,reason,appointment):
    if (any(type(x) is not UUID for x in (ident,key))
            or stage not in ("QUALIFIED","BOOKED","LOST")
            or (stage=="LOST")!=(reason is not None)
            or (reason is not None and reason not in ("PRICE","TIMING","NO_RESPONSE","OTHER"))
            or (stage=="BOOKED")!=(appointment is not None)
            or (appointment is not None and type(appointment) is not UUID)):
        raise LeadDenied("INVALID_INPUT")
    with conn.transaction():
        actor,manager=_authority(conn,token,org,loc)
        # Take the mutation lock first, avoiding two concurrent SHARE-to-UPDATE
        # upgrades. Scope and current owner are rechecked after the lock.
        conn.execute("""SELECT id FROM auto_crm_lead
                        WHERE id=%s AND organization_id=%s AND location_id=%s
                        FOR UPDATE""",(ident,org,loc))
        lead=_lead(conn,org,loc,ident,actor,manager)
        existing=conn.execute(
            """SELECT lead_id,actor_id,stage_code,reason_code,appointment_id
               FROM auto_crm_lead_stage WHERE request_key=%s""",(key,),
        ).fetchone()
        if existing:
            if existing!=(ident,actor,stage,reason,appointment):
                raise LeadConflict("KEY_REUSED")
            return {"replayed":True,**_state(conn,lead)}
        try:
            with conn.transaction():
                conn.execute(
                    """INSERT INTO auto_crm_lead_stage(
                       id,lead_id,actor_id,stage_code,reason_code,
                       appointment_id,request_key) VALUES(%s,%s,%s,%s,%s,%s,%s)""",
                    (uuid4(),ident,actor,stage,reason,appointment,key),
                )
        except (psycopg.errors.RaiseException,psycopg.errors.UniqueViolation,
                psycopg.errors.CheckViolation) as exc:
            raise LeadConflict("TRANSITION_CONFLICT") from exc
        return {"replayed":False,**_state(conn,lead)}


def lead_detail(conn,token,org,loc,ident):
    with conn.transaction():
        actor,manager=_authority(conn,token,org,loc)
        return _state(conn,_lead(conn,org,loc,ident,actor,manager))


LEAD_PAGE_SIZE=25


def _decode_lead_cursor(cursor):
    if cursor is None:
        return None
    if type(cursor) is not str or not (1<=len(cursor)<=180):
        raise LeadDenied("INVALID_INPUT")
    try:
        raw=base64.urlsafe_b64decode(cursor+"="*(-len(cursor)%4))
        if len(raw)>130 or base64.urlsafe_b64encode(raw).decode().rstrip("=")!=cursor:
            raise ValueError("non-canonical")
        stamp,ident=raw.decode("ascii").split("|")
        created=datetime.fromisoformat(stamp)
        key=UUID(ident)
        if (created.tzinfo is None or created.utcoffset() is None
                or str(key)!=ident):
            raise ValueError("invalid")
        return created,key
    except (ValueError,UnicodeDecodeError,binascii.Error,TypeError) as exc:
        raise LeadDenied("INVALID_INPUT") from exc


def _encode_lead_cursor(stamp,ident):
    return base64.urlsafe_b64encode(
        (stamp.isoformat()+"|"+str(ident)).encode("ascii")
    ).decode().rstrip("=")


def list_leads(conn,token,org,loc,cursor=None):
    with conn.transaction():
        actor,manager=_authority(conn,token,org,loc)
        after=_decode_lead_cursor(cursor)
        # Proof and tenant checks must precede LIMIT, not merely filter
        # an already truncated page in Python.
        rows=conn.execute(
            """SELECT lead.id,lead.customer_link_id,
                      COALESCE(assignment.assignee_id,lead.owner_id) AS owner_id,
                      lead.source_code,lead.request_key,lead.created_at
               FROM auto_crm_lead lead
               LEFT JOIN LATERAL(
                 SELECT assignee_id FROM auto_crm_lead_assignment_event
                 WHERE lead_id=lead.id ORDER BY created_at DESC,id DESC LIMIT 1
               ) assignment ON TRUE
               WHERE lead.organization_id=%s AND lead.location_id=%s
                 AND (%s OR COALESCE(assignment.assignee_id,lead.owner_id)=%s)
                 AND (%s::timestamptz IS NULL OR
                      (lead.created_at,lead.id)<(%s::timestamptz,%s::uuid))
                 AND EXISTS (
                   SELECT 1 FROM auto_vehicle_customer_link p
                   JOIN auto_vehicle_location_binding b
                     ON b.vehicle_id=p.vehicle_id AND b.location_id=lead.location_id
                   JOIN auto_service_location l
                     ON l.id=b.location_id AND l.organization_id=lead.organization_id
                     AND l.disabled_at IS NULL
                   JOIN auto_organization o ON o.id=l.organization_id
                     AND o.disabled_at IS NULL
                   WHERE p.id=lead.customer_link_id AND p.verified_at IS NOT NULL
                     AND p.verified_by IS NOT NULL AND p.revoked_at IS NULL
                 )
               ORDER BY lead.created_at DESC,lead.id DESC LIMIT %s""",
            (org,loc,manager,actor,after[0] if after else None,
             after[0] if after else None,after[1] if after else None,
             LEAD_PAGE_SIZE+1),
        ).fetchall()
        page=rows[:LEAD_PAGE_SIZE]
        # Every row is rechecked before serializing: revocation between
        # the SELECT and _state must not expose stale customer data.
        items=[_state(conn,_lead(conn,org,loc,row[0],actor,manager))
               for row in page]
        next_cursor=(
            _encode_lead_cursor(page[-1][5],page[-1][0])
            if len(rows)>LEAD_PAGE_SIZE else None
        )
        return {"syntheticOnly":True,"items":items,"nextCursor":next_cursor}
