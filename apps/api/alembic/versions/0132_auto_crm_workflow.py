"""Synthetic CRM workflow configuration, reassignment and consent-gated mock outbox.

Revision ID: 0132_auto_crm_workflow
Revises: 0131_auto_serial_receipt_link
"""
from alembic import op
from sqlalchemy import text

revision = "0132_auto_crm_workflow"
down_revision = "0131_auto_serial_receipt_link"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
      CREATE TABLE auto_crm_workflow_config_event (
        id uuid PRIMARY KEY,
        organization_id uuid NOT NULL REFERENCES auto_organization(id),
        location_id uuid NOT NULL REFERENCES auto_service_location(id),
        version integer NOT NULL CHECK(version BETWEEN 1 AND 2147483647),
        config jsonb NOT NULL,
        actor_id uuid NOT NULL REFERENCES app_user(id),
        request_key uuid NOT NULL UNIQUE,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        UNIQUE(organization_id,location_id,version),
        CHECK(jsonb_typeof(config)='object'),
        CHECK(jsonb_typeof(config->'stages')='array')
      )
    """)
    op.execute("""
      CREATE FUNCTION guard_auto_crm_workflow_config() RETURNS trigger
      LANGUAGE plpgsql AS $f$
      DECLARE n integer; distinct_n integer;
      BEGIN
        IF TG_OP<>'INSERT' THEN
          RAISE EXCEPTION 'CRM workflow configuration is append-only';
        END IF;
        IF NOT EXISTS(
          SELECT 1 FROM auto_service_location l
          JOIN auto_organization o ON o.id=l.organization_id
          WHERE l.id=NEW.location_id AND l.organization_id=NEW.organization_id
            AND l.disabled_at IS NULL AND o.disabled_at IS NULL
          FOR SHARE OF l,o
        ) THEN RAISE EXCEPTION 'CRM workflow scope unavailable'; END IF;
        IF NOT EXISTS(
          SELECT 1 FROM auto_organization_role_grant g
          JOIN app_user u ON u.id=g.user_id AND u.status='ACTIVE'
          WHERE g.organization_id=NEW.organization_id
            AND g.user_id=NEW.actor_id AND g.role_code='MANAGER'
            AND (g.location_id IS NULL OR g.location_id=NEW.location_id)
            AND g.revoked_at IS NULL
            AND (g.expires_at IS NULL OR g.expires_at>now())
          FOR SHARE OF g,u
        ) THEN RAISE EXCEPTION 'CRM workflow requires live MANAGER'; END IF;
        SELECT count(*),count(DISTINCT item->>'code')
          INTO n,distinct_n FROM jsonb_array_elements(NEW.config->'stages') AS stage_items(item);
        IF n<>3 OR distinct_n<>3 OR EXISTS(
          SELECT 1 FROM jsonb_array_elements(NEW.config->'stages') AS stage_items(item)
          WHERE item->>'code' NOT IN ('QUALIFIED','BOOKED','LOST')
             OR jsonb_typeof(item->'label') IS DISTINCT FROM 'string'
             OR length(item->>'label') NOT BETWEEN 1 AND 60
             OR btrim(item->>'label')=''
             OR (item->>'position') !~ '^[0-9]{1,2}$'
             OR jsonb_typeof(item->'enabled') IS DISTINCT FROM 'boolean'
             OR jsonb_typeof(item->'allowedFrom') IS DISTINCT FROM 'array'
             OR jsonb_typeof(item->'lossReasons') IS DISTINCT FROM 'array'
        ) THEN RAISE EXCEPTION 'CRM workflow config schema invalid'; END IF;
        IF EXISTS(
          SELECT 1 FROM jsonb_array_elements(NEW.config->'stages') AS stage_items(item)
          WHERE (item->>'code'='QUALIFIED'
                 AND item->'allowedFrom'<>'["NEW"]'::jsonb)
             OR (item->>'code'='BOOKED'
                 AND item->'allowedFrom'<>'["QUALIFIED"]'::jsonb)
             OR (item->>'code'='LOST'
                 AND item->'allowedFrom'<>'["NEW","QUALIFIED"]'::jsonb)
             OR (item->>'code'<>'LOST'
                 AND item->'lossReasons'<>'[]'::jsonb)
             OR (item->>'code'='LOST'
                 AND NOT (item->'lossReasons'<@'["PRICE","TIMING","NO_RESPONSE","OTHER"]'::jsonb))
        ) THEN RAISE EXCEPTION 'CRM workflow transition config invalid'; END IF;
        IF (SELECT count(DISTINCT (item->>'position')::integer)
            FROM jsonb_array_elements(NEW.config->'stages') AS stage_items(item))<>3
        THEN RAISE EXCEPTION 'CRM workflow positions must be unique'; END IF;
        IF NEW.version<>(SELECT COALESCE(max(version),0)+1
          FROM auto_crm_workflow_config_event
          WHERE organization_id=NEW.organization_id AND location_id=NEW.location_id)
        THEN RAISE EXCEPTION 'CRM workflow version mismatch'; END IF;
        RETURN NEW;
      END $f$
    """)
    op.execute("""
      CREATE TRIGGER auto_crm_workflow_config_guard
      BEFORE INSERT OR UPDATE OR DELETE ON auto_crm_workflow_config_event
      FOR EACH ROW EXECUTE FUNCTION guard_auto_crm_workflow_config()
    """)
    op.execute("""
      CREATE FUNCTION guard_auto_crm_stage_config() RETURNS trigger
      LANGUAGE plpgsql AS $f$
      DECLARE lead_row record; current_stage text; current_config jsonb;
              target_config jsonb;
      BEGIN
        SELECT organization_id,location_id INTO lead_row
          FROM auto_crm_lead WHERE id=NEW.lead_id;
        IF NOT FOUND THEN RAISE EXCEPTION 'CRM lead unavailable'; END IF;
        SELECT config INTO current_config
          FROM auto_crm_workflow_config_event
          WHERE organization_id=lead_row.organization_id
            AND location_id=lead_row.location_id
          ORDER BY version DESC LIMIT 1;
        IF current_config IS NULL THEN RETURN NEW; END IF;
        SELECT item INTO target_config
          FROM jsonb_array_elements(current_config->'stages') AS stage_items(item)
          WHERE item->>'code'=NEW.stage_code;
        IF target_config IS NULL OR target_config->>'enabled'<>'true'
        THEN RAISE EXCEPTION 'CRM stage disabled'; END IF;
        IF NEW.stage_code='LOST' AND (NEW.reason_code IS NULL OR
           NOT (target_config->'lossReasons' ? NEW.reason_code))
        THEN RAISE EXCEPTION 'CRM loss reason disabled'; END IF;
        RETURN NEW;
      END $f$
    """)
    op.execute("""
      CREATE TRIGGER auto_crm_stage_config_guard
      BEFORE INSERT ON auto_crm_lead_stage
      FOR EACH ROW EXECUTE FUNCTION guard_auto_crm_stage_config()
    """)
    op.execute("""CREATE INDEX ix_auto_crm_workflow_latest
      ON auto_crm_workflow_config_event(organization_id,location_id,version DESC)""")

    op.execute("""
      CREATE TABLE auto_crm_lead_assignment_event (
        id uuid PRIMARY KEY,
        lead_id uuid NOT NULL REFERENCES auto_crm_lead(id),
        organization_id uuid NOT NULL REFERENCES auto_organization(id),
        location_id uuid NOT NULL REFERENCES auto_service_location(id),
        assignee_id uuid NOT NULL REFERENCES app_user(id),
        actor_id uuid NOT NULL REFERENCES app_user(id),
        request_key uuid NOT NULL UNIQUE,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp()
      )
    """)
    op.execute("""CREATE INDEX ix_auto_crm_assignment_latest
      ON auto_crm_lead_assignment_event(lead_id,created_at DESC,id DESC)""")
    op.execute("""
      CREATE OR REPLACE FUNCTION guard_auto_crm_followup_task() RETURNS trigger
      LANGUAGE plpgsql AS $f$
      DECLARE lead_row record; current_stage text; current_owner uuid;
      BEGIN
        IF TG_OP<>'INSERT' THEN
          RAISE EXCEPTION 'CRM follow-up tasks are immutable'; END IF;
        SELECT organization_id,location_id,customer_link_id,owner_id
          INTO lead_row FROM auto_crm_lead WHERE id=NEW.lead_id FOR UPDATE;
        IF NOT FOUND THEN RAISE EXCEPTION 'CRM lead unavailable'; END IF;
        SELECT assignee_id INTO current_owner
          FROM auto_crm_lead_assignment_event
          WHERE lead_id=NEW.lead_id ORDER BY created_at DESC,id DESC LIMIT 1;
        IF NEW.owner_id<>COALESCE(current_owner,lead_row.owner_id) THEN
          RAISE EXCEPTION 'CRM follow-up must belong to current lead owner'; END IF;
        IF NOT EXISTS(
          SELECT 1 FROM auto_vehicle_customer_link proof
          JOIN auto_vehicle_location_binding binding
            ON binding.vehicle_id=proof.vehicle_id
            AND binding.location_id=lead_row.location_id
          JOIN auto_service_location loc
            ON loc.id=binding.location_id
            AND loc.organization_id=lead_row.organization_id
            AND loc.disabled_at IS NULL
          JOIN auto_organization org ON org.id=loc.organization_id
            AND org.disabled_at IS NULL
          WHERE proof.id=lead_row.customer_link_id
            AND proof.verified_at IS NOT NULL AND proof.verified_by IS NOT NULL
            AND proof.revoked_at IS NULL
          FOR SHARE OF proof,loc,org
        ) THEN RAISE EXCEPTION 'CRM follow-up customer proof unavailable'; END IF;
        SELECT stage_code INTO current_stage FROM auto_crm_lead_stage
          WHERE lead_id=NEW.lead_id ORDER BY created_at DESC,id DESC LIMIT 1;
        IF NEW.origin_work_order_id IS NULL THEN
          IF current_stage IN ('BOOKED','LOST') THEN
            RAISE EXCEPTION 'CRM follow-up requires active lead';
          END IF;
        ELSE
          IF NEW.task_code<>'SERVICE_REVIEW' OR current_stage IS DISTINCT FROM 'BOOKED'
             OR NOT EXISTS (
               SELECT 1 FROM auto_work_order w
               JOIN auto_appointment a ON a.id=w.appointment_id
               JOIN auto_work_order_execution e ON e.work_order_id=w.id
               JOIN auto_work_order_execution_finish f ON f.execution_id=e.id
               WHERE w.id=NEW.origin_work_order_id
                 AND w.organization_id=lead_row.organization_id
                 AND w.location_id=lead_row.location_id
                 AND a.organization_id=w.organization_id
                 AND a.location_id=w.location_id
                 AND a.customer_link_id=lead_row.customer_link_id
                 AND EXISTS(
                   SELECT 1 FROM auto_crm_lead_stage stage
                   WHERE stage.lead_id=NEW.lead_id AND stage.stage_code='BOOKED'
                     AND stage.appointment_id=a.id
                 )
                 AND EXISTS(
                   SELECT 1 FROM auto_work_order_qc_event qc
                   WHERE qc.work_order_id=w.id AND qc.result='PASS'
                     AND qc.created_at>=f.created_at
                     AND NOT EXISTS(
                       SELECT 1 FROM auto_work_order_qc_event newer
                       WHERE newer.work_order_id=w.id
                         AND (newer.created_at,newer.id)>(qc.created_at,qc.id)
                     )
                 )
               FOR SHARE OF w,a,e,f
             )
          THEN RAISE EXCEPTION 'CRM service review requires finished QC-passed booked order';
          END IF;
        END IF;
        RETURN NEW;
      END $f$
    """)
    op.execute("""
      CREATE FUNCTION guard_auto_crm_lead_assignment() RETURNS trigger
      LANGUAGE plpgsql AS $f$
      DECLARE lead_row record;
      BEGIN
        IF TG_OP<>'INSERT' THEN
          RAISE EXCEPTION 'CRM lead assignment history is append-only';
        END IF;
        SELECT organization_id,location_id,customer_link_id
          INTO lead_row FROM auto_crm_lead WHERE id=NEW.lead_id FOR UPDATE;
        IF NOT FOUND OR lead_row.organization_id<>NEW.organization_id
           OR lead_row.location_id<>NEW.location_id
        THEN RAISE EXCEPTION 'CRM assignment scope mismatch'; END IF;
        IF NOT EXISTS(
          SELECT 1 FROM auto_organization_role_grant g
          JOIN app_user u ON u.id=g.user_id AND u.status='ACTIVE'
          WHERE g.organization_id=NEW.organization_id AND g.user_id=NEW.actor_id
            AND g.role_code='MANAGER'
            AND (g.location_id IS NULL OR g.location_id=NEW.location_id)
            AND g.revoked_at IS NULL AND (g.expires_at IS NULL OR g.expires_at>now())
          FOR SHARE OF g,u
        ) THEN RAISE EXCEPTION 'CRM assignment requires live MANAGER'; END IF;
        IF NOT EXISTS(
          SELECT 1 FROM auto_organization_role_grant g
          JOIN app_user u ON u.id=g.user_id AND u.status='ACTIVE'
          WHERE g.organization_id=NEW.organization_id AND g.user_id=NEW.assignee_id
            AND g.role_code IN ('MANAGER','INTAKE')
            AND (g.location_id IS NULL OR g.location_id=NEW.location_id)
            AND g.revoked_at IS NULL AND (g.expires_at IS NULL OR g.expires_at>now())
          FOR SHARE OF g,u
        ) THEN RAISE EXCEPTION 'CRM assignee grant unavailable'; END IF;
        IF NOT EXISTS(
          SELECT 1 FROM auto_vehicle_customer_link p
          JOIN auto_vehicle_location_binding b ON b.vehicle_id=p.vehicle_id
            AND b.location_id=NEW.location_id
          JOIN auto_service_location l ON l.id=b.location_id
            AND l.organization_id=NEW.organization_id AND l.disabled_at IS NULL
          WHERE p.id=lead_row.customer_link_id AND p.verified_at IS NOT NULL
            AND p.verified_by IS NOT NULL AND p.revoked_at IS NULL
          FOR SHARE OF p,l
        ) THEN RAISE EXCEPTION 'CRM assignment proof revoked'; END IF;
        RETURN NEW;
      END $f$
    """)
    op.execute("""
      CREATE TRIGGER auto_crm_lead_assignment_guard
      BEFORE INSERT OR UPDATE OR DELETE ON auto_crm_lead_assignment_event
      FOR EACH ROW EXECUTE FUNCTION guard_auto_crm_lead_assignment()
    """)

    op.execute("""
      CREATE TABLE auto_crm_contact_consent_event (
        id uuid PRIMARY KEY,
        user_id uuid NOT NULL REFERENCES app_user(id),
        channel_code text NOT NULL CHECK(channel_code IN ('CALL','EMAIL','SMS')),
        purpose_code text NOT NULL CHECK(purpose_code='SERVICE_UPDATE'),
        enabled boolean NOT NULL,
        version integer NOT NULL CHECK(version BETWEEN 1 AND 2147483647),
        evidence_code text NOT NULL CHECK(evidence_code='SYNTHETIC_SELF_SERVICE'),
        request_key uuid NOT NULL UNIQUE,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        UNIQUE(user_id,channel_code,purpose_code,version)
      )
    """)
    op.execute("""CREATE INDEX ix_auto_crm_contact_consent_latest
      ON auto_crm_contact_consent_event(user_id,channel_code,purpose_code,version DESC)""")
    op.execute("""
      CREATE FUNCTION guard_auto_crm_contact_consent() RETURNS trigger
      LANGUAGE plpgsql AS $f$
      DECLARE latest_version integer;
      BEGIN
        IF TG_OP<>'INSERT' THEN
          RAISE EXCEPTION 'CRM contact consent history is append-only';
        END IF;
        PERFORM pg_advisory_xact_lock(hashtextextended(NEW.user_id::text,10102));
        IF NOT EXISTS(
          SELECT 1 FROM user_role role
          WHERE role.user_id=NEW.user_id AND role.role_code='CLIENT'
            AND role.revoked_at IS NULL AND (role.expires_at IS NULL OR role.expires_at>now())
          FOR SHARE OF role
        ) THEN RAISE EXCEPTION 'CRM consent requires active CLIENT'; END IF;
        SELECT COALESCE(max(version),0) INTO latest_version
          FROM auto_crm_contact_consent_event
          WHERE user_id=NEW.user_id AND channel_code=NEW.channel_code
            AND purpose_code=NEW.purpose_code;
        IF NEW.version<>latest_version+1 THEN
          RAISE EXCEPTION 'CRM consent version mismatch';
        END IF;
        RETURN NEW;
      END $f$
    """)
    op.execute("""
      CREATE TRIGGER auto_crm_contact_consent_guard
      BEFORE INSERT OR UPDATE OR DELETE ON auto_crm_contact_consent_event
      FOR EACH ROW EXECUTE FUNCTION guard_auto_crm_contact_consent()
    """)

    op.execute("""
      CREATE TABLE auto_crm_mock_outbox (
        id uuid PRIMARY KEY,
        lead_id uuid NOT NULL REFERENCES auto_crm_lead(id),
        organization_id uuid NOT NULL REFERENCES auto_organization(id),
        location_id uuid NOT NULL REFERENCES auto_service_location(id),
        channel_code text NOT NULL CHECK(channel_code IN ('CALL','EMAIL','SMS')),
        template_code text NOT NULL CHECK(template_code IN
          ('BOOKING_REMINDER','SERVICE_UPDATE','POST_SERVICE_REVIEW')),
        consent_event_ids uuid[] NOT NULL CHECK(cardinality(consent_event_ids)>0),
        actor_id uuid NOT NULL REFERENCES app_user(id),
        request_key uuid NOT NULL UNIQUE,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp()
      )
    """)
    op.execute("""CREATE INDEX ix_auto_crm_mock_outbox_lead
      ON auto_crm_mock_outbox(lead_id,created_at DESC,id DESC)""")
    op.execute("""
      CREATE FUNCTION guard_auto_crm_mock_outbox() RETURNS trigger
      LANGUAGE plpgsql AS $f$
      DECLARE lead_row record; expected_ids uuid[]; active_user uuid;
      BEGIN
        IF TG_OP<>'INSERT' THEN
          RAISE EXCEPTION 'CRM mock outbox is append-only';
        END IF;
        SELECT lead.organization_id,lead.location_id,lead.customer_link_id,
               proof.customer_id
          INTO lead_row
          FROM auto_crm_lead lead
          JOIN auto_vehicle_customer_link proof ON proof.id=lead.customer_link_id
          WHERE lead.id=NEW.lead_id FOR UPDATE OF lead,proof;
        IF NOT FOUND OR lead_row.organization_id<>NEW.organization_id
           OR lead_row.location_id<>NEW.location_id
        THEN RAISE EXCEPTION 'CRM mock outbox scope mismatch'; END IF;
        IF NOT EXISTS(
          SELECT 1 FROM auto_organization_role_grant g
          JOIN app_user u ON u.id=g.user_id AND u.status='ACTIVE'
          WHERE g.organization_id=NEW.organization_id AND g.user_id=NEW.actor_id
            AND g.role_code IN ('MANAGER','INTAKE')
            AND (g.location_id IS NULL OR g.location_id=NEW.location_id)
            AND g.revoked_at IS NULL AND (g.expires_at IS NULL OR g.expires_at>now())
          FOR SHARE OF g,u
        ) THEN RAISE EXCEPTION 'CRM mock outbox requires live staff grant'; END IF;
        IF NOT EXISTS(
          SELECT 1 FROM auto_vehicle_location_binding b
          JOIN auto_service_location l ON l.id=b.location_id
            AND l.organization_id=NEW.organization_id AND l.disabled_at IS NULL
          JOIN auto_organization o ON o.id=l.organization_id AND o.disabled_at IS NULL
          WHERE b.vehicle_id=(SELECT vehicle_id FROM auto_vehicle_customer_link WHERE id=lead_row.customer_link_id)
            AND b.location_id=NEW.location_id
        ) OR NOT EXISTS(
          SELECT 1 FROM auto_vehicle_customer_link p
          WHERE p.id=lead_row.customer_link_id AND p.verified_at IS NOT NULL
            AND p.verified_by IS NOT NULL AND p.revoked_at IS NULL
          FOR SHARE
        ) THEN RAISE EXCEPTION 'CRM mock outbox proof unavailable'; END IF;
        -- Serialize consent updates and lock active account/role rows until commit.
        FOR active_user IN
          SELECT DISTINCT acl.user_id
          FROM account_customer_link acl
          JOIN user_role role ON role.user_id=acl.user_id
          WHERE acl.customer_id=lead_row.customer_id AND acl.revoked_at IS NULL
            AND role.role_code='CLIENT' AND role.revoked_at IS NULL
            AND (role.expires_at IS NULL OR role.expires_at>now())
          ORDER BY acl.user_id
        LOOP
          PERFORM pg_advisory_xact_lock(hashtextextended(active_user::text,10102));
        END LOOP;
        PERFORM 1 FROM account_customer_link acl
          WHERE acl.customer_id=lead_row.customer_id AND acl.revoked_at IS NULL
          FOR SHARE OF acl;
        PERFORM 1 FROM user_role role
          WHERE role.user_id IN (
            SELECT acl.user_id FROM account_customer_link acl
            WHERE acl.customer_id=lead_row.customer_id AND acl.revoked_at IS NULL
          ) AND role.role_code='CLIENT' AND role.revoked_at IS NULL
            AND (role.expires_at IS NULL OR role.expires_at>now())
          FOR SHARE OF role;
        WITH active_users AS (
          SELECT DISTINCT acl.user_id
          FROM account_customer_link acl
          JOIN user_role role ON role.user_id=acl.user_id
          WHERE acl.customer_id=lead_row.customer_id AND acl.revoked_at IS NULL
            AND role.role_code='CLIENT' AND role.revoked_at IS NULL
            AND (role.expires_at IS NULL OR role.expires_at>now())
        ), latest AS (
          SELECT DISTINCT ON (consent.user_id) consent.id,consent.user_id,consent.enabled
          FROM auto_crm_contact_consent_event consent
          JOIN active_users users ON users.user_id=consent.user_id
          WHERE consent.channel_code=NEW.channel_code
            AND consent.purpose_code='SERVICE_UPDATE'
          ORDER BY consent.user_id,consent.version DESC
        )
        SELECT array_agg(latest.id ORDER BY latest.user_id) INTO expected_ids
        FROM active_users users JOIN latest ON latest.user_id=users.user_id
        WHERE latest.enabled IS TRUE;
        IF expected_ids IS NULL OR cardinality(expected_ids)=0
           OR expected_ids<>NEW.consent_event_ids
           OR (SELECT count(*) FROM (
             SELECT DISTINCT acl.user_id FROM account_customer_link acl
             JOIN user_role role ON role.user_id=acl.user_id
             WHERE acl.customer_id=lead_row.customer_id AND acl.revoked_at IS NULL
               AND role.role_code='CLIENT' AND role.revoked_at IS NULL
               AND (role.expires_at IS NULL OR role.expires_at>now())
           ) all_users)<>cardinality(expected_ids)
        THEN RAISE EXCEPTION 'CRM mock outbox requires current consent'; END IF;
        RETURN NEW;
      END $f$
    """)
    op.execute("""
      CREATE TRIGGER auto_crm_mock_outbox_guard
      BEFORE INSERT OR UPDATE OR DELETE ON auto_crm_mock_outbox
      FOR EACH ROW EXECUTE FUNCTION guard_auto_crm_mock_outbox()
    """)

    for table in ("auto_crm_workflow_config_event","auto_crm_lead_assignment_event",
                  "auto_crm_contact_consent_event","auto_crm_mock_outbox"):
        op.execute(f"REVOKE ALL ON {table} FROM PUBLIC")


def downgrade():
    bind=op.get_bind()
    for table in ("auto_crm_workflow_config_event","auto_crm_lead_assignment_event",
                  "auto_crm_contact_consent_event","auto_crm_mock_outbox"):
        if bind.execute(text(f"SELECT 1 FROM {table} LIMIT 1")).first():
            raise RuntimeError("CRM workflow evidence exists: restore isolated synthetic backup")
    op.execute("DROP TRIGGER auto_crm_mock_outbox_guard ON auto_crm_mock_outbox")
    op.execute("DROP TRIGGER auto_crm_contact_consent_guard ON auto_crm_contact_consent_event")
    op.execute("DROP TRIGGER auto_crm_lead_assignment_guard ON auto_crm_lead_assignment_event")
    op.execute("DROP TRIGGER auto_crm_stage_config_guard ON auto_crm_lead_stage")
    op.execute("DROP TRIGGER auto_crm_workflow_config_guard ON auto_crm_workflow_config_event")
    for function in ("guard_auto_crm_mock_outbox","guard_auto_crm_contact_consent",
                     "guard_auto_crm_lead_assignment","guard_auto_crm_stage_config",
                     "guard_auto_crm_workflow_config"):
        op.execute(f"DROP FUNCTION {function}()")
    op.execute("DROP TABLE auto_crm_mock_outbox")
    op.execute("DROP TABLE auto_crm_contact_consent_event")
    op.execute("""
      CREATE OR REPLACE FUNCTION guard_auto_crm_followup_task() RETURNS trigger
      LANGUAGE plpgsql AS $f$
      DECLARE lead_row record; current_stage text;
      BEGIN
        IF TG_OP<>'INSERT' THEN
          RAISE EXCEPTION 'CRM follow-up tasks are immutable'; END IF;
        SELECT organization_id,location_id,customer_link_id,owner_id
          INTO lead_row FROM auto_crm_lead WHERE id=NEW.lead_id FOR UPDATE;
        IF NOT FOUND THEN RAISE EXCEPTION 'CRM lead unavailable'; END IF;
        IF NEW.owner_id<>lead_row.owner_id THEN
          RAISE EXCEPTION 'CRM follow-up must belong to lead owner'; END IF;
        IF NOT EXISTS(
          SELECT 1 FROM auto_vehicle_customer_link proof
          JOIN auto_vehicle_location_binding binding
            ON binding.vehicle_id=proof.vehicle_id
            AND binding.location_id=lead_row.location_id
          JOIN auto_service_location loc
            ON loc.id=binding.location_id
            AND loc.organization_id=lead_row.organization_id
            AND loc.disabled_at IS NULL
          JOIN auto_organization org ON org.id=loc.organization_id
            AND org.disabled_at IS NULL
          WHERE proof.id=lead_row.customer_link_id
            AND proof.verified_at IS NOT NULL AND proof.verified_by IS NOT NULL
            AND proof.revoked_at IS NULL
          FOR SHARE OF proof,loc,org
        ) THEN RAISE EXCEPTION 'CRM follow-up customer proof unavailable'; END IF;
        SELECT stage_code INTO current_stage FROM auto_crm_lead_stage
          WHERE lead_id=NEW.lead_id ORDER BY created_at DESC,id DESC LIMIT 1;
        IF current_stage IN ('BOOKED','LOST') THEN
          RAISE EXCEPTION 'CRM follow-up requires active lead'; END IF;
        RETURN NEW;
      END $f$
    """)
    op.execute("DROP TABLE auto_crm_lead_assignment_event")
    op.execute("DROP TABLE auto_crm_workflow_config_event")
