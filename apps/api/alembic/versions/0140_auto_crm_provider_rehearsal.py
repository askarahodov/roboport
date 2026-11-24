"""Inert CRM provider adapter evidence: no addresses, transport or real delivery."""
from alembic import op
from sqlalchemy import text

revision="0140_auto_crm_provider_rehearsal"
down_revision="0139_auto_crm_survey"
branch_labels=None
depends_on=None


def _channels(messenger):
    allowed="'CALL','EMAIL','SMS'"+(",'MESSENGER'" if messenger else "")
    for table in ("auto_crm_contact_consent_event","auto_crm_mock_outbox"):
        op.execute(f"ALTER TABLE {table} DROP CONSTRAINT {table}_channel_code_check")
        op.execute(f"ALTER TABLE {table} ADD CONSTRAINT {table}_channel_code_check CHECK(channel_code IN ({allowed}))")


def upgrade():
    _channels(True)
    op.execute("""
      CREATE TABLE auto_crm_mock_call (
        id uuid PRIMARY KEY,
        provider_event_id uuid NOT NULL UNIQUE,
        lead_id uuid NOT NULL REFERENCES auto_crm_lead(id),
        actor_id uuid NOT NULL REFERENCES app_user(id),
        disposition text NOT NULL CHECK(disposition IN ('ANSWERED','MISSED')),
        created_at timestamptz NOT NULL DEFAULT clock_timestamp()
      );
      CREATE INDEX ix_auto_crm_mock_call_lead ON auto_crm_mock_call(lead_id,created_at DESC,id DESC);
      CREATE TABLE auto_crm_mock_attempt (
        id uuid PRIMARY KEY,
        message_id uuid NOT NULL REFERENCES auto_crm_mock_outbox(id),
        actor_id uuid NOT NULL REFERENCES app_user(id),
        request_key uuid NOT NULL UNIQUE,
        ordinal integer NOT NULL CHECK(ordinal BETWEEN 1 AND 3),
        outcome text NOT NULL CHECK(outcome IN ('ACCEPTED','TIMEOUT','RATE_LIMIT','REJECTED')),
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        UNIQUE(message_id,ordinal)
      );
      CREATE INDEX ix_auto_crm_mock_attempt_message ON auto_crm_mock_attempt(message_id,ordinal);
    """)
    op.execute("""
      CREATE FUNCTION guard_auto_crm_provider_rehearsal() RETURNS trigger LANGUAGE plpgsql AS $f$
      DECLARE origin record; message record; last_attempt record; uid uuid; expected uuid[]; origin_lead uuid;
      BEGIN
        IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'Mock provider audit is immutable'; END IF;
        IF TG_TABLE_NAME='auto_crm_mock_attempt' THEN
          SELECT * INTO message FROM auto_crm_mock_outbox WHERE id=NEW.message_id;
          IF NOT FOUND THEN RAISE EXCEPTION 'Mock message unavailable'; END IF;
          origin_lead:=message.lead_id;
        ELSE
          origin_lead:=NEW.lead_id;
        END IF;
        SELECT l.id,l.organization_id,l.location_id,p.customer_id,l.source_code,
               COALESCE((SELECT e.assignee_id FROM auto_crm_lead_assignment_event e
                         WHERE e.lead_id=l.id ORDER BY e.created_at DESC,e.id DESC LIMIT 1),l.owner_id) AS owner_id
          INTO origin FROM auto_crm_lead l
          JOIN auto_vehicle_customer_link p ON p.id=l.customer_link_id
            AND p.verified_at IS NOT NULL AND p.verified_by IS NOT NULL AND p.revoked_at IS NULL
          JOIN auto_vehicle_location_binding b ON b.vehicle_id=p.vehicle_id AND b.location_id=l.location_id
          JOIN auto_service_location loc ON loc.id=l.location_id AND loc.organization_id=l.organization_id AND loc.disabled_at IS NULL
          JOIN auto_organization o ON o.id=l.organization_id AND o.disabled_at IS NULL
          WHERE l.id=origin_lead
          FOR UPDATE OF l FOR SHARE OF p,b,loc,o;
        IF NOT FOUND OR NOT EXISTS(
          SELECT 1 FROM auto_organization_role_grant g JOIN app_user u ON u.id=g.user_id AND u.status='ACTIVE'
          WHERE g.user_id=NEW.actor_id AND g.organization_id=origin.organization_id AND g.revoked_at IS NULL
            AND (g.location_id IS NULL OR g.location_id=origin.location_id)
            AND (g.role_code='MANAGER' OR (g.role_code='INTAKE' AND origin.owner_id=NEW.actor_id))
          FOR SHARE OF g,u
        ) THEN RAISE EXCEPTION 'Mock provider scope unavailable'; END IF;
        NEW.created_at:=clock_timestamp();
        IF TG_TABLE_NAME='auto_crm_mock_call' THEN
          IF origin.source_code<>'PHONE' THEN RAISE EXCEPTION 'Call requires phone lead'; END IF;
          RETURN NEW;
        END IF;
        IF message.organization_id<>origin.organization_id OR message.location_id<>origin.location_id THEN
          RAISE EXCEPTION 'Mock message scope mismatch'; END IF;
        -- Same consent serialization as existing outbox, including multiple customer accounts.
        FOR uid IN SELECT DISTINCT acl.user_id FROM account_customer_link acl
          JOIN user_role role ON role.user_id=acl.user_id
          WHERE acl.customer_id=origin.customer_id AND acl.revoked_at IS NULL
            AND role.role_code='CLIENT' AND role.revoked_at IS NULL
            AND (role.expires_at IS NULL OR role.expires_at>now()) ORDER BY acl.user_id
        LOOP
          PERFORM pg_advisory_xact_lock(hashtextextended(uid::text,10102));
        END LOOP;
        PERFORM 1 FROM account_customer_link acl JOIN user_role role ON role.user_id=acl.user_id
          WHERE acl.customer_id=origin.customer_id AND acl.revoked_at IS NULL AND role.role_code='CLIENT'
            AND role.revoked_at IS NULL AND (role.expires_at IS NULL OR role.expires_at>now()) FOR SHARE OF acl,role;
        WITH users AS (
          SELECT DISTINCT acl.user_id FROM account_customer_link acl
          JOIN user_role role ON role.user_id=acl.user_id
          WHERE acl.customer_id=origin.customer_id AND acl.revoked_at IS NULL
            AND role.role_code='CLIENT' AND role.revoked_at IS NULL AND (role.expires_at IS NULL OR role.expires_at>now())
        ), latest AS (
          SELECT DISTINCT ON (c.user_id) c.id,c.user_id,c.enabled
          FROM auto_crm_contact_consent_event c JOIN users u ON u.user_id=c.user_id
          WHERE c.channel_code=message.channel_code AND c.purpose_code='SERVICE_UPDATE'
          ORDER BY c.user_id,c.version DESC
        ) SELECT array_agg(latest.id ORDER BY users.user_id) INTO expected
          FROM users LEFT JOIN latest ON latest.user_id=users.user_id AND latest.enabled IS TRUE;
        IF expected IS NULL OR expected IS DISTINCT FROM message.consent_event_ids THEN
          RAISE EXCEPTION 'Mock provider consent changed'; END IF;
        SELECT ordinal,outcome INTO last_attempt FROM auto_crm_mock_attempt WHERE message_id=NEW.message_id ORDER BY ordinal DESC LIMIT 1;
        IF NEW.ordinal<>COALESCE(last_attempt.ordinal,0)+1 OR last_attempt.outcome IN ('ACCEPTED','REJECTED') THEN
          RAISE EXCEPTION 'Mock provider transition unavailable'; END IF;
        RETURN NEW;
      END $f$;
      CREATE TRIGGER auto_crm_mock_call_guard BEFORE INSERT OR UPDATE OR DELETE ON auto_crm_mock_call
        FOR EACH ROW EXECUTE FUNCTION guard_auto_crm_provider_rehearsal();
      CREATE TRIGGER auto_crm_mock_attempt_guard BEFORE INSERT OR UPDATE OR DELETE ON auto_crm_mock_attempt
        FOR EACH ROW EXECUTE FUNCTION guard_auto_crm_provider_rehearsal();
    """)
    op.execute("REVOKE ALL ON auto_crm_mock_call,auto_crm_mock_attempt FROM PUBLIC")


def downgrade():
    db=op.get_bind()
    if db.execute(text("""SELECT EXISTS(SELECT 1 FROM auto_crm_mock_call)
      OR EXISTS(SELECT 1 FROM auto_crm_mock_attempt)
      OR EXISTS(SELECT 1 FROM auto_crm_contact_consent_event WHERE channel_code='MESSENGER')
      OR EXISTS(SELECT 1 FROM auto_crm_mock_outbox WHERE channel_code='MESSENGER')""")).scalar():
        raise RuntimeError("Mock CRM provider evidence requires verified restore")
    op.execute("DROP TABLE auto_crm_mock_attempt; DROP TABLE auto_crm_mock_call; DROP FUNCTION guard_auto_crm_provider_rehearsal()")
    _channels(False)
