"""Pre-provisioned OIDC subject mapping, never auto-create accounts.

Revision ID: 0019_oidc_identity
Revises: 0018_customer_preferences
"""
from alembic import op
revision="0019_oidc_identity"
down_revision="0018_customer_preferences"
branch_labels=None
depends_on=None

def upgrade():
    op.execute("""
      CREATE TABLE oidc_identity (
        issuer text NOT NULL CHECK (issuer LIKE 'https://%' AND length(issuer)<=512),
        subject text NOT NULL CHECK (length(subject) BETWEEN 1 AND 255),
        user_id uuid NOT NULL REFERENCES app_user(id) ON DELETE RESTRICT,
        created_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY(issuer,subject),
        UNIQUE(issuer,user_id)
      )
    """)
    op.execute("""
      CREATE FUNCTION deny_oidc_identity_reassignment() RETURNS trigger LANGUAGE plpgsql AS $body$
      BEGIN
        IF NEW.issuer IS DISTINCT FROM OLD.issuer OR
           NEW.subject IS DISTINCT FROM OLD.subject OR
           NEW.user_id IS DISTINCT FROM OLD.user_id THEN
          RAISE EXCEPTION 'OIDC identity binding immutable';
        END IF;
        RETURN NEW;
      END $body$;
    """)
    op.execute("""
      CREATE TRIGGER oidc_identity_immutable BEFORE UPDATE ON oidc_identity
      FOR EACH ROW EXECUTE FUNCTION deny_oidc_identity_reassignment()
    """)

def downgrade():
    op.execute("DROP TABLE oidc_identity")
    op.execute("DROP FUNCTION deny_oidc_identity_reassignment()")
