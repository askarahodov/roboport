"""Version-specific estimate approval ledger; synthetic PostgreSQL only.

Revision ID: 0004_approval
Revises: 0003_idempotency
"""
from alembic import op

revision = "0004_approval"
down_revision = "0003_idempotency"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
      CREATE TABLE estimate_approval (
        id uuid PRIMARY KEY,
        estimate_id uuid NOT NULL REFERENCES estimate(id) ON DELETE RESTRICT,
        actor_id uuid NOT NULL,
        decision text NOT NULL CHECK (decision IN ('APPROVED','DECLINED')),
        created_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE(estimate_id)
      )
    """)
    op.execute("""
      CREATE FUNCTION guard_estimate_approval() RETURNS trigger LANGUAGE plpgsql AS $body$
      DECLARE
        quote_state text;
        quote_ticket uuid;
        quote_version integer;
        latest_version integer;
      BEGIN
        SELECT e.state,e.ticket_id,e.version
          INTO quote_state,quote_ticket,quote_version
          FROM estimate e WHERE e.id=NEW.estimate_id FOR UPDATE;
        IF NOT FOUND OR quote_state <> 'SENT' THEN
          RAISE EXCEPTION 'only SENT estimate may be approved';
        END IF;
        -- Lock the parent ticket to serialize decisions with creation of newer versions.
        PERFORM 1 FROM service_ticket WHERE id=quote_ticket FOR UPDATE;
        SELECT max(e.version) INTO latest_version FROM estimate e WHERE e.ticket_id=quote_ticket;
        IF quote_version <> latest_version THEN
          RAISE EXCEPTION 'stale estimate version';
        END IF;
        UPDATE estimate SET state=NEW.decision WHERE id=NEW.estimate_id;
        RETURN NEW;
      END $body$;
    """)
    op.execute("""
      CREATE TRIGGER estimate_approval_guard BEFORE INSERT ON estimate_approval
      FOR EACH ROW EXECUTE FUNCTION guard_estimate_approval()
    """)
    op.execute("""
      CREATE FUNCTION reject_estimate_approval_mutation() RETURNS trigger LANGUAGE plpgsql AS $body$
      BEGIN
        RAISE EXCEPTION 'estimate approval is append-only';
      END $body$;
    """)
    op.execute("""
      CREATE TRIGGER estimate_approval_append_only BEFORE UPDATE OR DELETE ON estimate_approval
      FOR EACH ROW EXECUTE FUNCTION reject_estimate_approval_mutation()
    """)


def downgrade():
    op.execute("DROP TABLE estimate_approval")
    op.execute("DROP FUNCTION guard_estimate_approval()")
    op.execute("DROP FUNCTION reject_estimate_approval_mutation()")
