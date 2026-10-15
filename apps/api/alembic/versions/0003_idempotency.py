"""Idempotency keys scoped by actor, request fingerprint and operation.

Revision ID: 0003_idempotency
Revises: 0002_estimate_stock
"""
from alembic import op

revision = "0003_idempotency"
down_revision = "0002_estimate_stock"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
      CREATE TABLE idempotency_request (
        id uuid PRIMARY KEY,
        actor_id uuid NOT NULL,
        operation text NOT NULL CHECK (length(trim(operation)) > 0),
        key_hash text NOT NULL CHECK (length(key_hash) = 64),
        request_fingerprint text NOT NULL CHECK (length(request_fingerprint) = 64),
        state text NOT NULL DEFAULT 'PROCESSING'
          CHECK (state IN ('PROCESSING','COMPLETED','FAILED')),
        response_code integer CHECK (response_code BETWEEN 100 AND 599),
        created_at timestamptz NOT NULL DEFAULT now(),
        expires_at timestamptz NOT NULL CHECK (expires_at > created_at),
        UNIQUE(actor_id,operation,key_hash),
        CONSTRAINT ck_completed_response CHECK (state <> 'COMPLETED' OR response_code IS NOT NULL)
      )
    """)
    op.execute("CREATE INDEX ix_idempotency_expiry ON idempotency_request(expires_at)")
    op.execute("""
      CREATE FUNCTION reject_idempotency_identity_change() RETURNS trigger LANGUAGE plpgsql AS $body$
      BEGIN
        IF (NEW.actor_id, NEW.operation, NEW.key_hash, NEW.request_fingerprint, NEW.created_at)
            IS DISTINCT FROM
           (OLD.actor_id, OLD.operation, OLD.key_hash, OLD.request_fingerprint, OLD.created_at) THEN
          RAISE EXCEPTION 'idempotency identity and fingerprint immutable';
        END IF;
        IF OLD.state = 'COMPLETED' AND (NEW.state, NEW.response_code)
           IS DISTINCT FROM (OLD.state, OLD.response_code) THEN
          RAISE EXCEPTION 'completed idempotency request immutable';
        END IF;
        RETURN NEW;
      END $body$;
    """)
    op.execute("""
      CREATE TRIGGER idempotency_identity_immutable BEFORE UPDATE ON idempotency_request
      FOR EACH ROW EXECUTE FUNCTION reject_idempotency_identity_change()
    """)


def downgrade():
    op.execute("DROP TABLE idempotency_request")
    op.execute("DROP FUNCTION reject_idempotency_identity_change()")
