"""Immutable inbound synthetic CRM interactions; no contact data or delivery."""
from alembic import op
from sqlalchemy import text

revision="0142_auto_crm_interactions"
down_revision="0141_auto_crm_repeat_cycles"
branch_labels=None
depends_on=None


def upgrade():
    op.execute("""
      CREATE TABLE auto_crm_interaction (
        id uuid PRIMARY KEY,
        lead_id uuid NOT NULL REFERENCES auto_crm_lead(id),
        organization_id uuid NOT NULL REFERENCES auto_organization(id),
        location_id uuid NOT NULL REFERENCES auto_service_location(id),
        owner_id uuid NOT NULL REFERENCES app_user(id),
        actor_id uuid NOT NULL REFERENCES app_user(id),
        source_code text NOT NULL CHECK(source_code IN ('WEB','PHONE','WALK_IN')),
        channel_code text NOT NULL CHECK(channel_code IN ('CALL','EMAIL','SMS','MESSENGER','WALK_IN')),
        outcome_code text NOT NULL CHECK(outcome_code IN
          ('REQUEST_RECEIVED','CALLBACK_REQUIRED','INFORMATION_REQUESTED','DECLINED')),
        request_key uuid NOT NULL UNIQUE,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp()
      );
      CREATE INDEX ix_auto_crm_interaction_lead
        ON auto_crm_interaction(lead_id,created_at DESC,id DESC);
      CREATE FUNCTION guard_auto_crm_interaction() RETURNS trigger
      LANGUAGE plpgsql AS $f$
      DECLARE lead_row record; effective_owner uuid;
      BEGIN
        IF TG_OP<>'INSERT' THEN
          RAISE EXCEPTION 'CRM interaction evidence immutable'; END IF;
        SELECT organization_id,location_id,customer_link_id,owner_id,source_code
          INTO lead_row FROM auto_crm_lead WHERE id=NEW.lead_id FOR UPDATE;
        IF NOT FOUND OR (lead_row.organization_id,lead_row.location_id,lead_row.source_code)
           IS DISTINCT FROM (NEW.organization_id,NEW.location_id,NEW.source_code)
        THEN RAISE EXCEPTION 'CRM interaction scope mismatch'; END IF;
        SELECT assignee_id INTO effective_owner FROM auto_crm_lead_assignment_event
          WHERE lead_id=NEW.lead_id ORDER BY created_at DESC,id DESC LIMIT 1;
        effective_owner=COALESCE(effective_owner,lead_row.owner_id);
        IF NEW.owner_id<>effective_owner THEN
          RAISE EXCEPTION 'CRM interaction requires current owner'; END IF;
        IF NOT EXISTS(
          SELECT 1 FROM auto_organization_role_grant g
          JOIN app_user u ON u.id=g.user_id AND u.status='ACTIVE'
          WHERE g.organization_id=NEW.organization_id AND g.user_id=NEW.actor_id
            AND (g.location_id IS NULL OR g.location_id=NEW.location_id)
            AND g.revoked_at IS NULL
            AND (g.role_code='MANAGER' OR (g.role_code='INTAKE' AND NEW.actor_id=effective_owner))
          FOR SHARE OF g,u
        ) THEN RAISE EXCEPTION 'CRM interaction requires live staff grant'; END IF;
        IF NOT EXISTS(
          SELECT 1 FROM auto_vehicle_customer_link p
          JOIN auto_vehicle_location_binding b ON b.vehicle_id=p.vehicle_id
            AND b.location_id=NEW.location_id
          JOIN auto_service_location l ON l.id=b.location_id
            AND l.organization_id=NEW.organization_id AND l.disabled_at IS NULL
          JOIN auto_organization o ON o.id=l.organization_id AND o.disabled_at IS NULL
          WHERE p.id=lead_row.customer_link_id AND p.verified_at IS NOT NULL
            AND p.verified_by IS NOT NULL AND p.revoked_at IS NULL
          FOR SHARE OF p,b,l,o
        ) THEN RAISE EXCEPTION 'CRM interaction requires verified proof'; END IF;
        RETURN NEW;
      END $f$;
      CREATE TRIGGER auto_crm_interaction_guard
        BEFORE INSERT OR UPDATE OR DELETE ON auto_crm_interaction
        FOR EACH ROW EXECUTE FUNCTION guard_auto_crm_interaction();
      REVOKE ALL ON auto_crm_interaction FROM PUBLIC;
    """)


def downgrade():
    if op.get_bind().execute(text("SELECT 1 FROM auto_crm_interaction LIMIT 1")).first():
        raise RuntimeError("CRM interaction evidence exists: restore isolated synthetic backup")
    op.execute("DROP TRIGGER auto_crm_interaction_guard ON auto_crm_interaction")
    op.execute("DROP FUNCTION guard_auto_crm_interaction()")
    op.execute("DROP TABLE auto_crm_interaction")
