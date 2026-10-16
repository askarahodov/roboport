"""In-app customer notification preferences, synthetic only.

Revision ID: 0018_customer_preferences
Revises: 0017_outbox
"""
from alembic import op

revision="0018_customer_preferences"
down_revision="0017_outbox"
branch_labels=None
depends_on=None

def upgrade():
    op.execute("""CREATE TABLE user_session_csrf_rotation(
      session_id uuid PRIMARY KEY REFERENCES user_session(id) ON DELETE RESTRICT,
      csrf_hash char(64) NOT NULL CHECK(csrf_hash ~ '^[0-9a-f]{64}
      user_id uuid PRIMARY KEY REFERENCES app_user(id) ON DELETE RESTRICT,
      in_app_enabled boolean NOT NULL DEFAULT true,
      updated_at timestamptz NOT NULL DEFAULT now()
    )""")
    op.execute("""CREATE TABLE customer_preference_audit(
      id uuid PRIMARY KEY,
      user_id uuid NOT NULL REFERENCES app_user(id) ON DELETE RESTRICT,
      in_app_enabled boolean NOT NULL,
      changed_at timestamptz NOT NULL DEFAULT now()
    )""")
    op.execute("""CREATE FUNCTION reject_preference_audit_mutation() RETURNS trigger
      LANGUAGE plpgsql AS $body$ BEGIN
        RAISE EXCEPTION 'preference audit append only';
      END $body$""")
    op.execute("""CREATE TRIGGER preference_audit_immutable
      BEFORE UPDATE OR DELETE ON customer_preference_audit
      FOR EACH ROW EXECUTE FUNCTION reject_preference_audit_mutation()""")

def downgrade():
    op.execute("DROP TABLE user_session_csrf_rotation")
    op.execute("DROP TABLE customer_preference_audit")
    op.execute("DROP TABLE customer_notification_preference")
    op.execute("DROP FUNCTION reject_preference_audit_mutation()")
),
      rotated_at timestamptz NOT NULL DEFAULT now()
    )""")
    op.execute("""CREATE TABLE customer_notification_preference(
      user_id uuid PRIMARY KEY REFERENCES app_user(id) ON DELETE RESTRICT,
      in_app_enabled boolean NOT NULL DEFAULT true,
      updated_at timestamptz NOT NULL DEFAULT now()
    )""")
    op.execute("""CREATE TABLE customer_preference_audit(
      id uuid PRIMARY KEY,
      user_id uuid NOT NULL REFERENCES app_user(id) ON DELETE RESTRICT,
      in_app_enabled boolean NOT NULL,
      changed_at timestamptz NOT NULL DEFAULT now()
    )""")
    op.execute("""CREATE FUNCTION reject_preference_audit_mutation() RETURNS trigger
      LANGUAGE plpgsql AS $body$ BEGIN
        RAISE EXCEPTION 'preference audit append only';
      END $body$""")
    op.execute("""CREATE TRIGGER preference_audit_immutable
      BEFORE UPDATE OR DELETE ON customer_preference_audit
      FOR EACH ROW EXECUTE FUNCTION reject_preference_audit_mutation()""")

def downgrade():
    op.execute("DROP TABLE customer_preference_audit")
    op.execute("DROP TABLE customer_notification_preference")
    op.execute("DROP FUNCTION reject_preference_audit_mutation()")
