"""Synthetic branch-scoped CRM cases and append-only stage history.

Revision ID: 0098_auto_crm_lead
Revises: 0097_auto_booking_service_origin

No PII, outbound messaging, production consent or contact channels.
"""
from alembic import op
from sqlalchemy import text

revision="0098_auto_crm_lead"
down_revision="0097_auto_booking_service_origin"
branch_labels=None
depends_on=None


def upgrade():
    op.execute("""
      CREATE TABLE auto_crm_lead (
        id uuid PRIMARY KEY,
        organization_id uuid NOT NULL REFERENCES auto_organization(id),
        location_id uuid NOT NULL REFERENCES auto_service_location(id),
        owner_id uuid NOT NULL REFERENCES app_user(id),
        created_by uuid NOT NULL REFERENCES app_user(id),
        source_code text NOT NULL CHECK (source_code IN
          ('WALK_IN','PHONE_NOT_RECORDED','WEB_FORM_TEST','STAFF_REFERRAL')),
        intake_key uuid NOT NULL,
        request_key uuid NOT NULL UNIQUE,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        UNIQUE (organization_id,location_id,intake_key)
      )
    """)
    op.execute("""
      CREATE TABLE auto_crm_lead_booking (
        lead_id uuid PRIMARY KEY REFERENCES auto_crm_lead(id),
        appointment_id uuid NOT NULL UNIQUE REFERENCES auto_appointment(id),
        actor_id uuid NOT NULL REFERENCES app_user(id),
        request_key uuid NOT NULL UNIQUE,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp()
      )
    """)
    op.execute("""
      CREATE TABLE auto_crm_lead_stage_event (
        id uuid PRIMARY KEY,
        lead_id uuid NOT NULL REFERENCES auto_crm_lead(id),
        actor_id uuid NOT NULL REFERENCES app_user(id),
        request_key uuid NOT NULL UNIQUE,
        stage text NOT NULL CHECK(stage IN
          ('NEW','CONTACTED','BOOKED','WON','LOST')),
        reason_code text NULL CHECK(reason_code IN
          ('NO_RESPONSE','CUSTOMER_DECLINED','DUPLICATE','OTHER')),
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        CONSTRAINT auto_crm_lead_stage_reason CHECK (
          (stage='LOST' AND reason_code IS NOT NULL)
          OR (stage<>'LOST' AND reason_code IS NULL))
      )
    """)
    op.execute("""
      CREATE INDEX ix_auto_crm_lead_scope
        ON auto_crm_lead(organization_id,location_id,created_at DESC,id DESC)
    """)
    op.execute("""
      CREATE INDEX ix_auto_crm_stage_history
        ON auto_crm_lead_stage_event(lead_id,created_at DESC,id DESC)
    """)
    op.execute("""
      CREATE FUNCTION guard_auto_crm_lead()
      RETURNS trigger LANGUAGE plpgsql AS $fn$
      BEGIN
        IF TG_OP<>'INSERT' THEN
          RAISE EXCEPTION 'crm lead identity immutable';
        END IF;
        IF NOT EXISTS(
          SELECT 1 FROM auto_organization o
          JOIN auto_service_location l ON l.organization_id=o.id
          JOIN auto_organization_role_grant g
            ON g.organization_id=o.id AND g.user_id=NEW.owner_id
          WHERE o.id=NEW.organization_id AND l.id=NEW.location_id
            AND o.disabled_at IS NULL AND l.disabled_at IS NULL
            AND g.revoked_at IS NULL AND g.role_code IN ('MANAGER','INTAKE')
            AND (g.location_id=NEW.location_id OR g.location_id IS NULL)
          FOR SHARE OF o,l,g
        ) THEN RAISE EXCEPTION 'crm owner must have live staff role'; END IF;
        RETURN NEW;
      END $fn$
    """)
    op.execute("""
      CREATE TRIGGER auto_crm_lead_guard BEFORE INSERT OR UPDATE OR DELETE
        ON auto_crm_lead FOR EACH ROW EXECUTE FUNCTION guard_auto_crm_lead()
    """)
    op.execute("""
      CREATE FUNCTION guard_auto_crm_lead_booking()
      RETURNS trigger LANGUAGE plpgsql AS $fn$
      BEGIN
        IF TG_OP<>'INSERT' THEN
          RAISE EXCEPTION 'crm booking link immutable'; END IF;
        IF NOT EXISTS(
          SELECT 1 FROM auto_crm_lead l
          JOIN auto_appointment a ON a.id=NEW.appointment_id
          WHERE l.id=NEW.lead_id AND a.organization_id=l.organization_id
            AND a.location_id=l.location_id FOR UPDATE OF l
        ) THEN RAISE EXCEPTION 'crm appointment scope invalid'; END IF;
        RETURN NEW;
      END $fn$
    """)
    op.execute("""
      CREATE TRIGGER auto_crm_booking_guard
      BEFORE INSERT OR UPDATE OR DELETE ON auto_crm_lead_booking
      FOR EACH ROW EXECUTE FUNCTION guard_auto_crm_lead_booking()
    """)
    op.execute("""
      CREATE FUNCTION guard_auto_crm_stage()
      RETURNS trigger LANGUAGE plpgsql AS $fn$
      DECLARE previous text; linked uuid;
      BEGIN
        IF TG_OP<>'INSERT' THEN
          RAISE EXCEPTION 'crm stage history immutable';
        END IF;
        SELECT id INTO linked FROM auto_crm_lead
        WHERE id=NEW.lead_id FOR UPDATE;
        IF NOT FOUND THEN RAISE EXCEPTION 'crm lead unavailable'; END IF;
        SELECT stage INTO previous FROM auto_crm_lead_stage_event
        WHERE lead_id=NEW.lead_id ORDER BY created_at DESC,id DESC LIMIT 1;
        IF (previous IS NULL AND NEW.stage<>'NEW')
          OR (previous='NEW' AND NEW.stage NOT IN ('CONTACTED','LOST'))
          OR (previous='CONTACTED' AND NEW.stage NOT IN ('BOOKED','LOST'))
          OR (previous='BOOKED' AND NEW.stage NOT IN ('WON','LOST'))
          OR previous IN ('WON','LOST')
        THEN RAISE EXCEPTION 'crm illegal stage transition'; END IF;
        IF NEW.stage IN ('BOOKED','WON') AND NOT EXISTS(
          SELECT 1 FROM auto_crm_lead_booking
          WHERE lead_id=NEW.lead_id
        )
        THEN RAISE EXCEPTION 'crm appointment required'; END IF;
        RETURN NEW;
      END $fn$
    """)
    op.execute("""
      CREATE TRIGGER auto_crm_stage_guard BEFORE INSERT OR UPDATE OR DELETE
        ON auto_crm_lead_stage_event FOR EACH ROW
        EXECUTE FUNCTION guard_auto_crm_stage()
    """)
    for table in ("auto_crm_lead","auto_crm_lead_booking","auto_crm_lead_stage_event"):
        op.execute(f"REVOKE ALL ON {table} FROM PUBLIC")


def downgrade():
    if op.get_bind().execute(text("SELECT 1 FROM auto_crm_lead LIMIT 1")).first():
        raise RuntimeError("crm evidence exists: restore disposable backup")
    op.execute("DROP TABLE auto_crm_lead_stage_event")
    op.execute("DROP TABLE auto_crm_lead_booking")
    op.execute("DROP TABLE auto_crm_lead")
    op.execute("DROP FUNCTION guard_auto_crm_stage()")
    op.execute("DROP FUNCTION guard_auto_crm_lead_booking()")
    op.execute("DROP FUNCTION guard_auto_crm_lead()")
