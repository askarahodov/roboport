"""Scoped synthetic CRM workflow configuration, assignment and Kanban.

All operations are private dev-only. No customer contact data is returned.
"""
from uuid import UUID, uuid4
import psycopg

from .auto_crm_leads import (
    LeadDenied, LeadConflict, _authority, _lead, _state,
)

STAGE_CODES=("QUALIFIED","BOOKED","LOST")
LOSS_REASONS=("PRICE","TIMING","NO_RESPONSE","OTHER")
DEFAULT_CONFIG={
    "stages":[
        {"code":"QUALIFIED","label":"Обработано","position":10,
         "enabled":True,"allowedFrom":["NEW"],"lossReasons":[]},
        {"code":"BOOKED","label":"Записан","position":20,
         "enabled":True,"allowedFrom":["QUALIFIED"],"lossReasons":[]},
        {"code":"LOST","label":"Отказ","position":30,
         "enabled":True,"allowedFrom":["NEW","QUALIFIED"],
         "lossReasons":list(LOSS_REASONS)},
    ]
}


def _validate_config(config):
    if type(config) is not dict or set(config)!={"stages"} or type(config["stages"]) is not list:
        raise LeadDenied("INVALID_INPUT")
    if len(config["stages"])!=3:
        raise LeadDenied("INVALID_INPUT")
    expected={
        "QUALIFIED":["NEW"],
        "BOOKED":["QUALIFIED"],
        "LOST":["NEW","QUALIFIED"],
    }
    seen=set()
    positions=set()
    result=[]
    for item in config["stages"]:
        if type(item) is not dict or set(item)!={"code","label","position","enabled","allowedFrom","lossReasons"}:
            raise LeadDenied("INVALID_INPUT")
        code=item["code"]
        label=item["label"]
        position=item["position"]
        enabled=item["enabled"]
        allowed=item["allowedFrom"]
        reasons=item["lossReasons"]
        if (code not in STAGE_CODES or code in seen or
            type(label) is not str or not 1<=len(label.strip())<=60 or
            label!=label.strip() or any(ord(c)<32 for c in label) or
            type(position) is not int or not 0<=position<=99 or position in positions or
            type(enabled) is not bool or type(allowed) is not list or
            allowed!=expected[code] or type(reasons) is not list or
            any(type(reason) is not str or reason not in LOSS_REASONS for reason in reasons) or
            len(set(reasons))!=len(reasons) or
            (code!="LOST" and reasons)):
            raise LeadDenied("INVALID_INPUT")
        seen.add(code)
        positions.add(position)
        result.append({"code":code,"label":label,"position":position,
                       "enabled":enabled,"allowedFrom":allowed,
                       "lossReasons":reasons})
    if seen!=set(STAGE_CODES):
        raise LeadDenied("INVALID_INPUT")
    result.sort(key=lambda row:(row["position"],row["code"]))
    return {"stages":result}


def get_workflow_config(conn,token,org,loc):
    with conn.transaction():
        _,manager=_authority(conn,token,org,loc)
        row=conn.execute("""
          SELECT version,config FROM auto_crm_workflow_config_event
          WHERE organization_id=%s AND location_id=%s
          ORDER BY version DESC LIMIT 1
        """,(org,loc)).fetchone()
        if row is None:
            return {"syntheticOnly":True,"version":0,"canConfigure":manager,"config":DEFAULT_CONFIG}
        return {"syntheticOnly":True,"version":row[0],"canConfigure":manager,"config":row[1]}


def set_workflow_config(conn,token,org,loc,expected_revision,request_key,config):
    if (type(org) is not UUID or type(loc) is not UUID or
        type(request_key) is not UUID or type(expected_revision) is not int or
        not 0<=expected_revision<2147483647):
        raise LeadDenied("INVALID_INPUT")
    normalized=_validate_config(config)
    with conn.transaction():
        actor,manager=_authority(conn,token,org,loc)
        if not manager:
            raise LeadDenied("FORBIDDEN")
        conn.execute("""
          SELECT id FROM auto_service_location
          WHERE id=%s AND organization_id=%s AND disabled_at IS NULL
          FOR UPDATE
        """,(loc,org))
        existing=conn.execute("""
          SELECT organization_id,location_id,actor_id,config
          FROM auto_crm_workflow_config_event WHERE request_key=%s
        """,(request_key,)).fetchone()
        if existing:
            if existing!=(org,loc,actor,normalized):
                raise LeadConflict("KEY_REUSED")
            row=conn.execute("""
              SELECT version FROM auto_crm_workflow_config_event
              WHERE request_key=%s
            """,(request_key,)).fetchone()
            return {"replayed":True,"syntheticOnly":True,
                    "version":row[0],"config":normalized}
        current=conn.execute("""
          SELECT COALESCE(max(version),0) FROM auto_crm_workflow_config_event
          WHERE organization_id=%s AND location_id=%s
        """,(org,loc)).fetchone()[0]
        if current!=expected_revision:
            raise LeadConflict("STALE_REVISION")
        version=current+1
        conn.execute("""
          INSERT INTO auto_crm_workflow_config_event
            (id,organization_id,location_id,version,config,actor_id,request_key)
          VALUES(%s,%s,%s,%s,%s::jsonb,%s,%s)
        """,(uuid4(),org,loc,version,psycopg.types.json.Jsonb(normalized),actor,request_key))
        return {"replayed":False,"syntheticOnly":True,
                "version":version,"config":normalized}


def assign_lead(conn,token,org,loc,lead_id,assignee_id,request_key):
    if any(type(x) is not UUID for x in (org,loc,lead_id,assignee_id,request_key)):
        raise LeadDenied("INVALID_INPUT")
    with conn.transaction():
        actor,manager=_authority(conn,token,org,loc)
        if not manager:
            raise LeadDenied("FORBIDDEN")
        conn.execute("""
          SELECT id FROM auto_crm_lead
          WHERE id=%s AND organization_id=%s AND location_id=%s
          FOR UPDATE
        """,(lead_id,org,loc))
        lead=_lead(conn,org,loc,lead_id,actor,manager)
        existing=conn.execute("""
          SELECT lead_id,organization_id,location_id,assignee_id,actor_id
          FROM auto_crm_lead_assignment_event WHERE request_key=%s
        """,(request_key,)).fetchone()
        expected=(lead_id,org,loc,assignee_id,actor)
        if existing:
            if existing!=expected:
                raise LeadConflict("KEY_REUSED")
            return {"replayed":True,**_state(conn,lead)}
        if not conn.execute("""
          SELECT 1 FROM auto_organization_role_grant g
          JOIN app_user u ON u.id=g.user_id AND u.status='ACTIVE'
          WHERE g.organization_id=%s AND g.user_id=%s
            AND g.role_code IN ('MANAGER','INTAKE')
            AND (g.location_id IS NULL OR g.location_id=%s)
            AND g.revoked_at IS NULL

          FOR SHARE OF g,u
        """,(org,assignee_id,loc)).fetchone():
            raise LeadDenied("FORBIDDEN")
        try:
            conn.execute("""
              INSERT INTO auto_crm_lead_assignment_event
                (id,lead_id,organization_id,location_id,assignee_id,actor_id,request_key)
              VALUES(%s,%s,%s,%s,%s,%s,%s)
            """,(uuid4(),lead_id,org,loc,assignee_id,actor,request_key))
        except (psycopg.errors.UniqueViolation,psycopg.errors.RaiseException,
                psycopg.errors.CheckViolation,psycopg.errors.ForeignKeyViolation):
            raise LeadConflict("ASSIGNMENT_CONFLICT") from None
        return {"replayed":False,**_state(conn,_lead(conn,org,loc,lead_id,actor,manager))}


def list_kanban(conn,token,org,loc):
    if type(org) is not UUID or type(loc) is not UUID:
        raise LeadDenied("INVALID_INPUT")
    with conn.transaction():
        actor,manager=_authority(conn,token,org,loc)
        config_row=conn.execute("""
          SELECT version,config FROM auto_crm_workflow_config_event
          WHERE organization_id=%s AND location_id=%s
          ORDER BY version DESC LIMIT 1
        """,(org,loc)).fetchone()
        config=config_row[1] if config_row else DEFAULT_CONFIG
        version=config_row[0] if config_row else 0
        rows=conn.execute("""
          SELECT lead.id,lead.customer_link_id,
                 COALESCE(assignment.assignee_id,lead.owner_id) AS owner_id,
                 lead.source_code,lead.created_at,
                 COALESCE(stage.stage_code,'NEW') AS stage_code
          FROM auto_crm_lead lead
          LEFT JOIN LATERAL(
            SELECT assignee_id FROM auto_crm_lead_assignment_event
            WHERE lead_id=lead.id ORDER BY created_at DESC,id DESC LIMIT 1
          ) assignment ON TRUE
          LEFT JOIN LATERAL(
            SELECT stage_code FROM auto_crm_lead_stage
            WHERE lead_id=lead.id ORDER BY created_at DESC,id DESC LIMIT 1
          ) stage ON TRUE
          WHERE lead.organization_id=%s AND lead.location_id=%s
            AND (%s OR COALESCE(assignment.assignee_id,lead.owner_id)=%s)
            AND EXISTS(
              SELECT 1 FROM auto_vehicle_customer_link proof
              JOIN auto_vehicle_location_binding binding
                ON binding.vehicle_id=proof.vehicle_id AND binding.location_id=lead.location_id
              JOIN auto_service_location branch ON branch.id=binding.location_id
                AND branch.organization_id=lead.organization_id AND branch.disabled_at IS NULL
              JOIN auto_organization tenant ON tenant.id=branch.organization_id
                AND tenant.disabled_at IS NULL
              WHERE proof.id=lead.customer_link_id AND proof.verified_at IS NOT NULL
                AND proof.verified_by IS NOT NULL AND proof.revoked_at IS NULL
            )
          ORDER BY lead.created_at DESC,lead.id DESC LIMIT 201
        """,(org,loc,manager,actor)).fetchall()
        if len(rows)>200:
            raise LeadConflict("BOARD_LIMIT")
        columns=[{"code":"NEW","label":"Новое","position":0,"enabled":True,
                  "allowedFrom":[],"lossReasons":[]}]
        columns.extend(config["stages"])
        columns.sort(key=lambda row:(row["position"],row["code"]))
        items=[]
        for lead_id,link,owner,source,created,stage in rows:
            # Recheck live proof and effective assignee after the board snapshot.
            lead=_lead(conn,org,loc,lead_id,actor,manager)
            items.append({
                "leadId":str(lead_id),"customerLinkId":str(link),
                "ownerId":str(lead[2]),"sourceCode":source,
                "stageCode":stage,"createdAt":created.isoformat(),
            })
        return {"syntheticOnly":True,"version":version,
                "columns":columns,"items":items,"limit":200,"canConfigure":manager}
