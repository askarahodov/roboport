"""Deny-by-default role grants, object grants and append-only authorization audit."""
from alembic import op

revision = "0008_authorization"
down_revision = "0007_auth_throttle"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE TABLE role (
          code text PRIMARY KEY CHECK (code IN (
            'PUBLIC','CLIENT','INTAKE','ENGINEER','STOCK',
            'MANAGER','FINANCE','ADMIN','AUDITOR'))
        )
    """)
    op.execute("""
        INSERT INTO role(code) VALUES ('PUBLIC'),('CLIENT'),('INTAKE'),
          ('ENGINEER'),('STOCK'),('MANAGER'),('FINANCE'),('ADMIN'),('AUDITOR')
    """)
    op.execute("""
        CREATE TABLE user_role (
          id uuid PRIMARY KEY,
          user_id uuid NOT NULL REFERENCES app_user(id) ON DELETE RESTRICT,
          role_code text NOT NULL REFERENCES role(code) ON DELETE RESTRICT,
          granted_at timestamptz NOT NULL DEFAULT now(),
          expires_at timestamptz,
          revoked_at timestamptz,
          CHECK (expires_at IS NULL OR expires_at > granted_at)
        )
    """)
    op.execute("CREATE INDEX ix_user_role_active ON user_role(user_id,role_code) WHERE revoked_at IS NULL")
    op.execute("""
        CREATE TABLE acl_entry (
          id uuid PRIMARY KEY,
          user_id uuid NOT NULL REFERENCES app_user(id) ON DELETE RESTRICT,
          ticket_id uuid NOT NULL REFERENCES service_ticket(id) ON DELETE RESTRICT,
          action text NOT NULL CHECK (action IN ('ticket.read','ticket.diagnose','ticket.intake','ticket.manage')),
          granted_at timestamptz NOT NULL DEFAULT now(),
          expires_at timestamptz,
          revoked_at timestamptz,
          CHECK (expires_at IS NULL OR expires_at > granted_at)
        )
    """)
    op.execute("CREATE INDEX ix_acl_subject_ticket ON acl_entry(user_id,ticket_id,action) WHERE revoked_at IS NULL")
    op.execute("""
        CREATE FUNCTION protect_access_revoke() RETURNS trigger LANGUAGE plpgsql AS $body$
        BEGIN
          IF TG_TABLE_NAME = 'user_role' THEN
            IF (NEW.user_id,NEW.role_code,NEW.granted_at,NEW.expires_at)
               IS DISTINCT FROM (OLD.user_id,OLD.role_code,OLD.granted_at,OLD.expires_at) THEN
              RAISE EXCEPTION 'role grant identity immutable';
            END IF;
          ELSIF TG_TABLE_NAME = 'acl_entry' THEN
            IF (NEW.user_id,NEW.ticket_id,NEW.action,NEW.granted_at,NEW.expires_at)
               IS DISTINCT FROM (OLD.user_id,OLD.ticket_id,OLD.action,OLD.granted_at,OLD.expires_at) THEN
              RAISE EXCEPTION 'ACL grant identity immutable';
            END IF;
          ELSE
            RAISE EXCEPTION 'unknown grant table';
          END IF;
          IF OLD.revoked_at IS NOT NULL AND NEW.revoked_at IS DISTINCT FROM OLD.revoked_at THEN
            RAISE EXCEPTION 'access revocation irreversible';
          END IF;
          RETURN NEW;
        END $body$;
    """)
    for table in ("user_role", "acl_entry"):
        op.execute(f"""CREATE TRIGGER {table}_immutable BEFORE UPDATE ON {table}
                       FOR EACH ROW EXECUTE FUNCTION protect_access_revoke()""")
    op.execute("""
        CREATE TABLE authorization_audit (
          id uuid PRIMARY KEY,
          actor_id uuid REFERENCES app_user(id) ON DELETE RESTRICT,
          ticket_id uuid REFERENCES service_ticket(id) ON DELETE RESTRICT,
          action text NOT NULL,
          outcome text NOT NULL CHECK (outcome IN ('ALLOW','DENY')),
          created_at timestamptz NOT NULL DEFAULT now()
        )
    """)
    op.execute("""CREATE TRIGGER authorization_audit_immutable BEFORE UPDATE OR DELETE
                  ON authorization_audit FOR EACH ROW EXECUTE FUNCTION reject_audit_mutation()""")


def downgrade():
    op.execute("DROP TABLE authorization_audit")
    op.execute("DROP TABLE acl_entry")
    op.execute("DROP TABLE user_role")
    op.execute("DROP TABLE role")
    op.execute("DROP FUNCTION protect_access_revoke()")
