"""Scoped synthetic CRM follow-up inbox: OPEN tasks across verified leads.

No customer PII, outbound notifications or persisted delivery queue.
"""
from uuid import UUID

from .auto_crm_followups import _task
from .auto_crm_leads import _authority, _lead, LeadConflict


def list_inbox(conn, token, org, loc):
    with conn.transaction():
        actor, manager = _authority(conn, token, org, loc)
        # The database filters active proof, tenant, role and open status
        # BEFORE taking the bounded page. Revoked tasks must not consume slots.
        rows = conn.execute(
            """SELECT t.lead_id,t.id
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
                 AND NOT EXISTS (
                   SELECT 1 FROM auto_crm_followup_event event
                   WHERE event.task_id=t.id
                 )
               ORDER BY t.due_at,t.id LIMIT 101""",
            (org, loc, manager, actor),
        ).fetchall()
        if len(rows)>100:
            raise LeadConflict("INBOX_LIMIT")
        items=[]
        for lead_id,task_id in rows:
            # Re-validate every item via the same protected single-lead read.
            _lead(conn, org, loc, lead_id, actor, manager)
            task=_task(conn, lead_id, task_id)
            # Another authorized employee may resolve the task between
            # SELECT and serialization under READ COMMITTED.
            if task["status"]=="OPEN":
                items.append({"leadId":str(lead_id),**task})
        return {"syntheticOnly":True,"items":items}
