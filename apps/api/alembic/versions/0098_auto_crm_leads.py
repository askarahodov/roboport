"""Synthetic CRM intake: scoped deduplicated leads + immutable stage evidence.

Revision ID: 0098_auto_crm_leads
Revises: 0097_auto_booking_service_origin
"""
from alembic import op
from sqlalchemy import text

revision="0098_auto_crm_leads"
down_revision="0097_auto_booking_service_origin"
branch_labels=None
depends_on=None


def upgrade():
    op.execute("""
      CREATE TABLE auto_crm_lead (
        id uuid PRIMARY KEY,
        organization_id uuid NOT NULL REFERENCES auto_organization(id),
        location_id uuid NOT NULL REFERENCES auto_service_location(id),
        customer_link_id uuid NOT NULL REFERENCES auto_vehicle_customer_link(id),
        owner_id uuid NOT NULL REFERENCES app_user(id),
        source_code text NOT NULL CHECK (source_code IN ('WEB','PHONE','WALK_IN')),
        request_key uuid NOT NULL UNIQUE,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        UNIQUE(organization_id,location_id,customer_link_id,source_code)
      )
    """)
    op.execute("""
      CREATE TABLE auto_crm_lead_stage (
        id uuid PRIMARY KEY,
        lead_id uuid NOT NULL REFERENCES auto_crm_lead(id),
        actor_id uuid NOT NULL REFERENCES app_user(id),
        stage_code text NOT NULL CHECK(stage_code IN ('QUALIFIED','BOOKED','LOST')),
        reason_code text CHECK(reason_code IN ('PRICE','TIMING','NO_RESPONSE','OTHER')),
        appointment_id uuid REFERENCES auto_appointment(id),
        request_key uuid NOT NULL UNIQUE,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        CHECK ((stage_code='LOST')=(reason_code IS NOT NULL)),
        CHECK ((stage_code='BOOKED')=(appointment_id IS NOT NULL))
      )
    """)
    op.execute("""CREATE INDEX ix_auto_crm_lead_scope
      ON auto_crm_lead(organization_id,location_id,created_at DESC,id DESC)""")
    op.execute("""CREATE INDEX ix_auto_crm_lead_stage_latest
      ON auto_crm_lead_stage(lead_id,created_at DESC,id DESC)""")
    op.execute("""
      CREATE FUNCTION guard_auto_crm_lead() RETURNS trigger
      LANGUAGE plpgsql AS $fn$
      BEGIN
        IF TG_OP<>'INSERT' THEN
          RAISE EXCEPTION 'CRM lead identity immutable';
        END IF;
        IF NOT EXISTS (
          SELECT 1 FROM auto_vehicle_customer_link proof
          JOIN auto_vehicle_location_binding binding
            ON binding.vehicle_id=proof.vehicle_id AND binding.location_id=NEW.location_id
          JOIN auto_service_location loc
            ON loc.id=binding.location_id AND loc.organization_id=NEW.organization_id
            AND loc.disabled_at IS NULL
          JOIN auto_organization org ON org.id=loc.organization_id
            AND org.disabled_at IS NULL
          WHERE proof.id=NEW.customer_link_id
            AND proof.verified_at IS NOT NULL
            AND proof.verified_by IS NOT NULL
            AND proof.revoked_at IS NULL
          FOR SHARE OF proof,loc,org
        ) THEN RAISE EXCEPTION 'CRM lead requires live verified branch link'; END IF;
        RETURN NEW;
      END $fn$
    """)
    op.execute("""
      CREATE TRIGGER auto_crm_lead_guard BEFORE INSERT OR UPDATE OR DELETE
      ON auto_crm_lead FOR EACH ROW EXECUTE FUNCTION guard_auto_crm_lead()
    """)
    op.execute("""
      CREATE FUNCTION guard_auto_crm_stage() RETURNS trigger
      LANGUAGE plpgsql AS $fn$
      DECLARE lead_row record; old_stage text;
      BEGIN
        IF TG_OP<>'INSERT' THEN
          RAISE EXCEPTION 'CRM stage evidence immutable';
        END IF;
        SELECT id,organization_id,location_id,customer_link_id
          INTO lead_row FROM auto_crm_lead WHERE id=NEW.lead_id FOR UPDATE;
        IF NOT FOUND THEN RAISE EXCEPTION 'CRM lead unavailable'; END IF;
        IF NOT EXISTS(
          SELECT 1 FROM auto_vehicle_customer_link proof
          JOIN auto_vehicle_location_binding binding
            ON binding.vehicle_id=proof.vehicle_id AND binding.location_id=lead_row.location_id
          JOIN auto_service_location loc
            ON loc.id=binding.location_id AND loc.organization_id=lead_row.organization_id
            AND loc.disabled_at IS NULL
          WHERE proof.id=lead_row.customer_link_id
            AND proof.verified_at IS NOT NULL AND proof.verified_by IS NOT NULL
            AND proof.revoked_at IS NULL
          FOR SHARE OF proof,loc
        ) THEN RAISE EXCEPTION 'CRM customer proof revoked'; END IF;
        SELECT stage_code INTO old_stage
          FROM auto_crm_lead_stage WHERE lead_id=NEW.lead_id
          ORDER BY created_at DESC,id DESC LIMIT 1;
        IF (old_stage IS NULL AND NEW.stage_code NOT IN ('QUALIFIED','LOST'))
          OR (old_stage='QUALIFIED' AND NEW.stage_code NOT IN ('BOOKED','LOST'))
          OR old_stage IN ('BOOKED','LOST')
        THEN RAISE EXCEPTION 'CRM stage transition denied'; END IF;
        IF NEW.stage_code='BOOKED' AND NOT EXISTS (
          SELECT 1 FROM auto_appointment a
          WHERE a.id=NEW.appointment_id
            AND a.organization_id=lead_row.organization_id
            AND a.location_id=lead_row.location_id
            AND a.customer_link_id=lead_row.customer_link_id
          FOR SHARE
        ) THEN RAISE EXCEPTION 'CRM booking scope mismatch'; END IF;
        RETURN NEW;
      END $fn$
    """)
    op.execute("""
      CREATE TRIGGER auto_crm_stage_guard BEFORE INSERT OR UPDATE OR DELETE
      ON auto_crm_lead_stage FOR EACH ROW EXECUTE FUNCTION guard_auto_crm_stage()
    """)
    for table in ("auto_crm_lead","auto_crm_lead_stage"):
        op.execute(f"REVOKE ALL ON {table} FROM PUBLIC")


def downgrade():
    if op.get_bind().execute(text("SELECT 1 FROM auto_crm_lead LIMIT 1")).first():
        raise RuntimeError("CRM evidence exists: only restore isolated synthetic backup")
    op.execute("DROP TRIGGER auto_crm_stage_guard ON auto_crm_lead_stage")
    op.execute("DROP TRIGGER auto_crm_lead_guard ON auto_crm_lead")
    op.execute("DROP FUNCTION guard_auto_crm_stage()")
    op.execute("DROP FUNCTION guard_auto_crm_lead()")
    op.execute("DROP TABLE auto_crm_lead_stage")
    op.execute("DROP TABLE auto_crm_lead")
