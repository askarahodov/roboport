"""Private synthetic CRM intake: one lead per verified link/source/branch.

No phones, message payloads or outbound integrations. Stages are append-only;
DB trigger independently enforces ordering and WorkOrder booking lineage.
"""
from uuid import UUID, uuid4
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
    row=conn.execute(
        """SELECT id,customer_link_id,owner_id,source_code,request_key,created_at
           FROM auto_crm_lead
           WHERE id=%s AND organization_id=%s AND location_id=%s
           FOR SHARE""",
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
    return {
        "leadId":str(lead[0]),"customerLinkId":str(lead[1]),
        "ownerId":str(lead[2]),"sourceCode":lead[3],
        "status":events[-1][2] if events else "NEW",
        "createdAt":lead[5].isoformat(),"syntheticOnly":True,
        "events":[{
            "id":str(i),"actorId":str(actor),"stageCode":stage,
            "reasonCode":reason,"appointmentId":str(appointment) if appointment else None,
            "createdAt":stamp.isoformat(),
        } for i,actor,stage,reason,appointment,key,stamp in events],
    }


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
        try:
            with conn.transaction():
                ident=uuid4()
                conn.execute(
                    """INSERT INTO auto_crm_lead(
                       id,organization_id,location_id,customer_link_id,
                       owner_id,source_code,request_key)
                       VALUES(%s,%s,%s,%s,%s,%s,%s)""",
                    (ident,org,loc,link,actor,source,key),
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
        lead=_lead(conn,org,loc,ident,actor,manager)
        # Row locking serializes competing transitions and reuse detection.
        conn.execute("SELECT id FROM auto_crm_lead WHERE id=%s FOR UPDATE",(ident,))
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


def list_leads(conn,token,org,loc):
    with conn.transaction():
        actor,manager=_authority(conn,token,org,loc)
        rows=conn.execute(
            """SELECT id,customer_link_id,owner_id,source_code,request_key,created_at
               FROM auto_crm_lead WHERE organization_id=%s AND location_id=%s
                 AND (%s OR owner_id=%s)
               ORDER BY created_at DESC,id DESC LIMIT 50""",
            (org,loc,manager,actor),
        ).fetchall()
        return {"syntheticOnly":True,"items":[_state(conn,row) for row in rows
                if _proof(conn,org,loc,row[1])]}
