"""Local identity and session schema; no public login endpoint yet.

Revision ID: 0006_local_identity
Revises: 0005_quote_race
"""
from alembic import op

revision = "0006_local_identity"
down_revision = "0005_quote_race"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
      CREATE TABLE app_user (
        id uuid PRIMARY KEY,
        normalized_login text NOT NULL UNIQUE
          CHECK (normalized_login = lower(trim(normalized_login)) AND length(normalized_login) BETWEEN 3 AND 128),
        status text NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE','DISABLED')),
        created_at timestamptz NOT NULL DEFAULT now(),
        password_changed_at timestamptz
      )
    """)
    op.execute("""
      CREATE TABLE local_credential (
        user_id uuid PRIMARY KEY REFERENCES app_user(id) ON DELETE RESTRICT,
        password_hash text NOT NULL CHECK (password_hash LIKE '$argon2id$%' AND length(password_hash) >= 50),
        must_change_password boolean NOT NULL DEFAULT true,
        updated_at timestamptz NOT NULL DEFAULT now()
      )
    """)
    op.execute("""
      CREATE TABLE user_session (
        id uuid PRIMARY KEY,
        user_id uuid NOT NULL REFERENCES app_user(id) ON DELETE RESTRICT,
        token_hash char(64) NOT NULL UNIQUE CHECK (token_hash ~ '^[0-9a-f]{64}$'),
        csrf_hash char(64) NOT NULL CHECK (csrf_hash ~ '^[0-9a-f]{64}$'),
        created_at timestamptz NOT NULL DEFAULT now(),
        last_seen_at timestamptz NOT NULL DEFAULT now(),
        idle_expires_at timestamptz NOT NULL,
        absolute_expires_at timestamptz NOT NULL,
        revoked_at timestamptz,
        CONSTRAINT ck_session_ttl CHECK (
          idle_expires_at > created_at AND absolute_expires_at > created_at
          AND idle_expires_at <= absolute_expires_at
        )
      )
    """)
    op.execute("CREATE INDEX ix_session_user ON user_session(user_id)")
    op.execute("""
      CREATE TABLE account_customer_link (
        user_id uuid NOT NULL REFERENCES app_user(id) ON DELETE RESTRICT,
        customer_id uuid NOT NULL REFERENCES customer(id) ON DELETE RESTRICT,
        created_at timestamptz NOT NULL DEFAULT now(),
        revoked_at timestamptz,
        PRIMARY KEY (user_id, customer_id)
      )
    """)
    op.execute("""
      CREATE FUNCTION protect_session_identity() RETURNS trigger LANGUAGE plpgsql AS $body$
      BEGIN
        IF (NEW.user_id, NEW.token_hash, NEW.csrf_hash, NEW.created_at)
           IS DISTINCT FROM (OLD.user_id, OLD.token_hash, OLD.csrf_hash, OLD.created_at) THEN
          RAISE EXCEPTION 'session identity immutable';
        END IF;
        IF OLD.revoked_at IS NOT NULL AND NEW.revoked_at IS DISTINCT FROM OLD.revoked_at THEN
          RAISE EXCEPTION 'revocation irreversible';
        END IF;
        RETURN NEW;
      END $body$;
    """)
    op.execute("""
      CREATE TRIGGER session_identity_guard BEFORE UPDATE ON user_session
      FOR EACH ROW EXECUTE FUNCTION protect_session_identity()
    """)


def downgrade():
    op.execute("DROP TABLE account_customer_link")
    op.execute("DROP TABLE user_session")
    op.execute("DROP TABLE local_credential")
    op.execute("DROP TABLE app_user")
    op.execute("DROP FUNCTION protect_session_identity()")
