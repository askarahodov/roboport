"""Scoped synthetic CRM inbox with bounded keyset paging and live ACL checks.

No customer PII, outbound notifications or persisted delivery queue.
The cursor is a non-secret position marker, never an authority grant.
"""
import base64
import binascii
from datetime import datetime
from uuid import UUID

from .auto_crm_followups import _task
from .auto_crm_leads import _authority, _lead, LeadDenied


PAGE_SIZE=25


def _decode_cursor(cursor):
    if cursor is None:
        return None
    if type(cursor) is not str or not (1 <= len(cursor) <= 180):
        raise LeadDenied("INVALID_INPUT")
    try:
        raw=base64.urlsafe_b64decode(cursor+"="*(-len(cursor)%4))
        if len(raw)>130 or base64.urlsafe_b64encode(raw).decode().rstrip("=")!=cursor:
            raise ValueError("non-canonical cursor")
        stamp,ident=raw.decode("ascii").split("|")
        due=datetime.fromisoformat(stamp)
        task=UUID(ident)
        if due.tzinfo is None or due.utcoffset() is None or str(task)!=ident:
            raise ValueError("invalid cursor")
        return due,task
    except (ValueError,UnicodeDecodeError,binascii.Error,TypeError) as exc:
        raise LeadDenied("INVALID_INPUT") from exc


def _encode_cursor(due,ident):
    return base64.urlsafe_b64encode(
        (due.isoformat()+"|"+str(ident)).encode("ascii")
    ).decode().rstrip("=")


def list_inbox(conn,token,org,loc,cursor=None,mode="ALL"):
    if mode not in ("ALL","OVERDUE") or type(mode) is not str:
        raise LeadDenied("INVALID_INPUT")
    with conn.transaction():
        actor,manager=_authority(conn,token,org,loc)
        after=_decode_cursor(cursor)
        # Limit AFTER filtering proof/tenant/owner/open status, not before.
        # The keyset is stable across concurrent inserts/resolutions;
        # each page rechecks current scope and proof independently.
        rows=conn.execute(
            """SELECT t.lead_id,t.id,t.due_at
               FROM auto_crm_followup_task t
               JOIN auto_crm_lead lead ON lead.id=t.lead_id
               JOIN auto_vehicle_customer_link proof
                 ON proof.id=lead.customer_link_id
               JOIN auto_vehicle_location_binding binding
                 ON binding.vehicle_id=proof.vehicle_id
                AND binding.location_id=lead.location_id
               JOIN auto_service_location location
                 ON location.id=lead.location_id
                AND location.organization_id=lead.organization_id
                AND location.disabled_at IS NULL
               JOIN auto_organization organization
                 ON organization.id=location.organization_id
                AND organization.disabled_at IS NULL
               WHERE lead.organization_id=%s AND lead.location_id=%s
                 AND (%s OR lead.owner_id=%s)
                 AND proof.verified_at IS NOT NULL
                 AND proof.verified_by IS NOT NULL
                 AND proof.revoked_at IS NULL
                 AND (%s='ALL' OR t.due_at<now())
                 AND (%s::timestamptz IS NULL OR (t.due_at,t.id)>(%s::timestamptz,%s::uuid))
                 AND NOT EXISTS (
                   SELECT 1 FROM auto_crm_followup_event event
                   WHERE event.task_id=t.id
                 )
               ORDER BY t.due_at,t.id LIMIT %s""",
            (org,loc,manager,actor,mode,
             after[0] if after else None,
             after[0] if after else None,
             after[1] if after else None,
             PAGE_SIZE+1),
        ).fetchall()
        # Look ahead one row but don't expose it until the next page.
        page=rows[:PAGE_SIZE]
        items=[]
        for lead_id,task_id,due_at in page:
            _lead(conn,org,loc,lead_id,actor,manager)
            task=_task(conn,lead_id,task_id)
            # Other staff may resolve tasks between SELECT and serializing.
            if task["status"]=="OPEN":
                items.append({"leadId":str(lead_id),**task})
        next_cursor=(
            _encode_cursor(page[-1][2],page[-1][1])
            if len(rows)>PAGE_SIZE else None
        )
        return {"syntheticOnly":True,"items":items,"nextCursor":next_cursor}
