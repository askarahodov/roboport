"""Synthetic CRM follow-up tasks with immutable terminal audit.

Revision ID: 0099_auto_crm_followups
Revises: 0098_auto_crm_leads
"""
from alembic import op
from sqlalchemy import text

revision="0099_auto_crm_followups"
down_revision="0098_auto_crm_leads"
branch_labels=None
depends_on=None


def upgrade():
    op.execute("""
      CREATE TABLE auto_crm_followup_task (
        id uuid PRIMARY KEY,
        lead_id uuid NOT NULL REFERENCES auto_crm_lead(id),
        owner_id uuid NOT NULL REFERENCES app_user(id),
        creator_id uuid NOT NULL REFERENCES app_user(id),
        task_code text NOT NULL CHECK(task_code IN ('CALL_BACK','CHECK_BOOKING','SERVICE_REVIEW')),
        due_at timestamptz NOT NULL,
        request_key uuid NOT NULL UNIQUE,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        CHECK(due_at>created_at),
        CHECK(due_at<=created_at+interval '90 days')
      )
    """)
    op.execute("""
      CREATE TABLE auto_crm_followup_event (
        id uuid PRIMARY KEY,
        task_id uuid NOT NULL UNIQUE REFERENCES auto_crm_followup_task(id),
        actor_id uuid NOT NULL REFERENCES app_user(id),
        action text NOT NULL CHECK(action IN ('DONE','CANCELED')),
        request_key uuid NOT NULL UNIQUE,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp()
      )
    """)
    op.execute("""CREATE INDEX ix_auto_crm_followup_lead
      ON auto_crm_followup_task(lead_id,due_at,id)""")
    op.execute("""
      CREATE FUNCTION guard_auto_crm_followup_task() RETURNS trigger
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
    op.execute("""
      CREATE TRIGGER auto_crm_followup_task_guard
      BEFORE INSERT OR UPDATE OR DELETE ON auto_crm_followup_task
      FOR EACH ROW EXECUTE FUNCTION guard_auto_crm_followup_task()
    """)
    op.execute("""
      CREATE FUNCTION guard_auto_crm_followup_event() RETURNS trigger
      LANGUAGE plpgsql AS $f$
      DECLARE lead_row record;
      BEGIN
        IF TG_OP<>'INSERT' THEN
          RAISE EXCEPTION 'CRM follow-up event immutable'; END IF;
        SELECT lead.customer_link_id,lead.organization_id,lead.location_id
          INTO lead_row FROM auto_crm_followup_task task
          JOIN auto_crm_lead lead ON lead.id=task.lead_id
          WHERE task.id=NEW.task_id FOR UPDATE OF task;
        IF NOT FOUND THEN RAISE EXCEPTION 'CRM follow-up unavailable'; END IF;
        IF NOT EXISTS(
          SELECT 1 FROM auto_vehicle_customer_link proof
          JOIN auto_vehicle_location_binding binding
            ON binding.vehicle_id=proof.vehicle_id
            AND binding.location_id=lead_row.location_id
          JOIN auto_service_location loc
            ON loc.id=binding.location_id
            AND loc.organization_id=lead_row.organization_id
            AND loc.disabled_at IS NULL
          WHERE proof.id=lead_row.customer_link_id AND proof.revoked_at IS NULL
            AND proof.verified_at IS NOT NULL AND proof.verified_by IS NOT NULL
          FOR SHARE OF proof,loc
        ) THEN RAISE EXCEPTION 'CRM follow-up proof revoked'; END IF;
        RETURN NEW;
      END $f$
    """)
    op.execute("""
      CREATE TRIGGER auto_crm_followup_event_guard
      BEFORE INSERT OR UPDATE OR DELETE ON auto_crm_followup_event
      FOR EACH ROW EXECUTE FUNCTION guard_auto_crm_followup_event()
    """)
    for table in ("auto_crm_followup_task","auto_crm_followup_event"):
        op.execute(f"REVOKE ALL ON {table} FROM PUBLIC")


def downgrade():
    bind=op.get_bind()
    if bind.execute(text("SELECT 1 FROM auto_crm_followup_task LIMIT 1")).first():
        raise RuntimeError("CRM follow-up evidence exists: restore isolated synthetic backup")
    op.execute("DROP TABLE auto_crm_followup_event")
    op.execute("DROP TABLE auto_crm_followup_task")
    op.execute("DROP FUNCTION guard_auto_crm_followup_event()")
    op.execute("DROP FUNCTION guard_auto_crm_followup_task()")
