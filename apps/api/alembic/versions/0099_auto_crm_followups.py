"""Append-only synthetic CRM follow-up tasks and completion evidence.

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
        actor_id uuid NOT NULL REFERENCES app_user(id),
        kind text NOT NULL CHECK (kind IN (
          'CALLBACK_NOT_SENT','CHECK_DOCUMENTS','CONFIRM_BOOKING',
          'SERVICE_REVIEW_NOT_SENT'
        )),
        due_on date NOT NULL,
        request_key uuid NOT NULL UNIQUE,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        UNIQUE (lead_id,kind,due_on)
      )
    """)
    op.execute("""
      CREATE TABLE auto_crm_followup_event (
        id uuid PRIMARY KEY,
        task_id uuid NOT NULL UNIQUE REFERENCES auto_crm_followup_task(id),
        actor_id uuid NOT NULL REFERENCES app_user(id),
        action text NOT NULL CHECK (action IN ('DONE','CANCELED')),
        reason_code text NULL CHECK (reason_code IN (
          'NO_LONGER_NEEDED','DUPLICATE','OTHER'
        )),
        request_key uuid NOT NULL UNIQUE,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        CHECK ((action='CANCELED')=(reason_code IS NOT NULL))
      )
    """)
    op.execute("""
      CREATE INDEX ix_auto_crm_followup_by_lead
      ON auto_crm_followup_task(lead_id,due_on,id)
    """)
    op.execute("""
      CREATE FUNCTION guard_auto_crm_followup_task()
      RETURNS trigger LANGUAGE plpgsql AS $fn$
      BEGIN
        IF TG_OP <> 'INSERT' THEN
          RAISE EXCEPTION 'CRM follow-up immutable'; END IF;
        IF NOT EXISTS(
          SELECT 1 FROM auto_crm_lead l
          JOIN auto_organization o ON o.id=l.organization_id
            AND o.disabled_at IS NULL
          JOIN auto_service_location loc ON loc.id=l.location_id
            AND loc.organization_id=o.id AND loc.disabled_at IS NULL
          JOIN auto_organization_role_grant g
            ON g.organization_id=o.id AND g.user_id=l.owner_id
            AND g.revoked_at IS NULL
            AND g.role_code IN ('MANAGER','INTAKE')
            AND (g.location_id=loc.id OR g.location_id IS NULL)
          WHERE l.id=NEW.lead_id AND NEW.owner_id=l.owner_id
          FOR SHARE OF l,o,loc,g
        ) THEN RAISE EXCEPTION 'CRM follow-up owner unavailable'; END IF;
        RETURN NEW;
      END $fn$
    """)
    op.execute("""
      CREATE TRIGGER auto_crm_followup_task_guard
      BEFORE INSERT OR UPDATE OR DELETE
      ON auto_crm_followup_task FOR EACH ROW
      EXECUTE FUNCTION guard_auto_crm_followup_task()
    """)
    op.execute("""
      CREATE FUNCTION guard_auto_crm_followup_event()
      RETURNS trigger LANGUAGE plpgsql AS $fn$
      BEGIN
        IF TG_OP <> 'INSERT' THEN
          RAISE EXCEPTION 'CRM follow-up audit immutable'; END IF;
        IF NOT EXISTS(
          SELECT 1 FROM auto_crm_followup_task t
          WHERE t.id=NEW.task_id FOR UPDATE
        ) THEN RAISE EXCEPTION 'CRM follow-up unavailable'; END IF;
        IF EXISTS(
          SELECT 1 FROM auto_crm_followup_event e WHERE e.task_id=NEW.task_id
        ) THEN RAISE EXCEPTION 'CRM follow-up already terminal'; END IF;
        RETURN NEW;
      END $fn$
    """)
    op.execute("""
      CREATE TRIGGER auto_crm_followup_event_guard
      BEFORE INSERT OR UPDATE OR DELETE
      ON auto_crm_followup_event FOR EACH ROW
      EXECUTE FUNCTION guard_auto_crm_followup_event()
    """)
    for table in ("auto_crm_followup_task","auto_crm_followup_event"):
        op.execute(f"REVOKE ALL ON {table} FROM PUBLIC")


def downgrade():
    if op.get_bind().execute(text(
        "SELECT 1 FROM auto_crm_followup_task LIMIT 1"
    )).first() is not None:
        raise RuntimeError("CRM follow-up evidence exists: restore isolated backup")
    op.execute("DROP TABLE auto_crm_followup_event")
    op.execute("DROP TABLE auto_crm_followup_task")
    op.execute("DROP FUNCTION guard_auto_crm_followup_event()")
    op.execute("DROP FUNCTION guard_auto_crm_followup_task()")
