"""Synthetic CRM contact-channel choices: immutable client audit, no outbound grants.

Revision ID: 0101_auto_crm_contact_choices
Revises: 0100_auto_crm_service_review
"""
from alembic import op
from sqlalchemy import text

revision="0101_auto_crm_contact_choices"
down_revision="0100_auto_crm_service_review"
branch_labels=None
depends_on=None


def upgrade():
    op.execute("""
      CREATE TABLE auto_crm_contact_choice_event (
        id uuid PRIMARY KEY,
        user_id uuid NOT NULL REFERENCES app_user(id),
        channel_code text NOT NULL CHECK(channel_code IN ('CALL','EMAIL','SMS')),
        enabled boolean NOT NULL,
        version integer NOT NULL CHECK(version BETWEEN 1 AND 2147483647),
        request_key uuid NOT NULL UNIQUE,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        UNIQUE(user_id,channel_code,version)
      )
    """)
    op.execute("""CREATE INDEX ix_auto_crm_contact_choice_latest
      ON auto_crm_contact_choice_event(user_id,channel_code,version DESC)""")
    op.execute("""
      CREATE FUNCTION guard_auto_crm_contact_choice() RETURNS trigger
      LANGUAGE plpgsql AS $f$
      DECLARE latest_version integer;
      BEGIN
        IF TG_OP<>'INSERT' THEN
          RAISE EXCEPTION 'CRM contact choices are immutable'; END IF;
        -- Direct DB INSERTs must serialize the same identity as HTTP writers,
        -- and cannot backdate history, impersonate a revoked CLIENT or skip versions.
        PERFORM 1 FROM app_user WHERE id=NEW.user_id FOR UPDATE;
        IF NOT FOUND OR NOT EXISTS (
          SELECT 1 FROM user_role role
          WHERE role.user_id=NEW.user_id AND role.role_code='CLIENT'
            AND role.revoked_at IS NULL
            AND (role.expires_at IS NULL OR role.expires_at>now())
          FOR SHARE OF role
        ) THEN RAISE EXCEPTION 'CRM contact choice requires active CLIENT'; END IF;
        SELECT COALESCE(MAX(version),0) INTO latest_version
        FROM auto_crm_contact_choice_event
        WHERE user_id=NEW.user_id AND channel_code=NEW.channel_code;
        IF NEW.version<>latest_version+1 THEN
          RAISE EXCEPTION 'CRM contact choice version mismatch'; END IF;
        RETURN NEW;
      END $f$
    """)
    op.execute("""CREATE TRIGGER auto_crm_contact_choice_guard
      BEFORE INSERT OR UPDATE OR DELETE ON auto_crm_contact_choice_event
      FOR EACH ROW EXECUTE FUNCTION guard_auto_crm_contact_choice()""")
    op.execute("REVOKE ALL ON auto_crm_contact_choice_event FROM PUBLIC")


def downgrade():
    if op.get_bind().execute(text(
        "SELECT 1 FROM auto_crm_contact_choice_event LIMIT 1"
    )).first():
        raise RuntimeError("CRM contact choice audit exists: restore isolated synthetic backup")
    op.execute("DROP TABLE auto_crm_contact_choice_event")
    op.execute("DROP FUNCTION guard_auto_crm_contact_choice()")
