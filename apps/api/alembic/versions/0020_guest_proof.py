"""Synthetic guest proof lifecycle; no claim or link is granted on intake.
Revision ID: 0020_guest_proof
Revises: 0019_oidc_identity
"""
from alembic import op
revision="0020_guest_proof"
down_revision="0019_oidc_identity"
branch_labels=None
depends_on=None

def upgrade():
    op.execute("""
      CREATE TABLE guest_ticket_claim (
        id uuid PRIMARY KEY,
        ticket_id uuid NOT NULL UNIQUE REFERENCES service_ticket(id) ON DELETE RESTRICT,
        customer_id uuid NOT NULL REFERENCES customer(id) ON DELETE RESTRICT,
        requested_user_id uuid REFERENCES app_user(id) ON DELETE RESTRICT,
        proof_hash char(64) NOT NULL CHECK (proof_hash ~ '^[0-9a-f]{64}$'),
        proof_expires_at timestamptz NOT NULL,
        proof_attempts integer NOT NULL DEFAULT 0 CHECK (proof_attempts BETWEEN 0 AND 5),
        proof_verified_at timestamptz,
        reviewed_by uuid REFERENCES app_user(id) ON DELETE RESTRICT,
        reviewed_at timestamptz,
        status text NOT NULL DEFAULT 'PENDING_REVIEW'
          CHECK (status IN ('PENDING_REVIEW','VERIFIED','DECLINED')),
        created_at timestamptz NOT NULL DEFAULT now(),
        CHECK ((proof_verified_at IS NULL) OR requested_user_id IS NOT NULL),
        CHECK ((status='PENDING_REVIEW' AND reviewed_at IS NULL AND reviewed_by IS NULL)
          OR (status<>'PENDING_REVIEW' AND reviewed_at IS NOT NULL AND reviewed_by IS NOT NULL)),
        CHECK (status<>'VERIFIED' OR proof_verified_at IS NOT NULL)
      )
    """)
    op.execute("CREATE INDEX ix_guest_claim_pending ON guest_ticket_claim(status,created_at) WHERE status='PENDING_REVIEW'")
    op.execute("""
      CREATE FUNCTION guard_guest_claim() RETURNS trigger LANGUAGE plpgsql AS $fn$
      BEGIN
        IF (NEW.id,NEW.ticket_id,NEW.customer_id,NEW.proof_hash,NEW.proof_expires_at,NEW.created_at)
           IS DISTINCT FROM
           (OLD.id,OLD.ticket_id,OLD.customer_id,OLD.proof_hash,OLD.proof_expires_at,OLD.created_at) THEN
          RAISE EXCEPTION 'guest claim identity immutable';
        END IF;
        IF OLD.status <> 'PENDING_REVIEW' AND NEW IS DISTINCT FROM OLD THEN
          RAISE EXCEPTION 'terminal claim cannot mutate';
        END IF;
        IF OLD.requested_user_id IS NOT NULL AND NEW.requested_user_id IS DISTINCT FROM OLD.requested_user_id THEN
          RAISE EXCEPTION 'claim user immutable';
        END IF;
        IF OLD.proof_verified_at IS NOT NULL AND NEW.proof_verified_at IS DISTINCT FROM OLD.proof_verified_at THEN
          RAISE EXCEPTION 'proof immutable';
        END IF;
        IF NEW.proof_attempts < OLD.proof_attempts THEN
          RAISE EXCEPTION 'attempts irreversible';
        END IF;
        RETURN NEW;
      END $fn$
    """)
    op.execute("""CREATE TRIGGER guest_claim_guard BEFORE UPDATE ON guest_ticket_claim
                  FOR EACH ROW EXECUTE FUNCTION guard_guest_claim()""")
    op.execute("""
      CREATE TABLE guest_claim_audit (
        id uuid PRIMARY KEY,
        claim_id uuid NOT NULL REFERENCES guest_ticket_claim(id) ON DELETE RESTRICT,
        actor_id uuid REFERENCES app_user(id) ON DELETE RESTRICT,
        action text NOT NULL CHECK (action IN ('INTAKE','PROOF_ACCEPTED','PROOF_REJECTED','VERIFIED','DECLINED')),
        created_at timestamptz NOT NULL DEFAULT now()
      )
    """)
    op.execute("""CREATE TRIGGER guest_claim_audit_immutable BEFORE UPDATE OR DELETE ON guest_claim_audit
                  FOR EACH ROW EXECUTE FUNCTION reject_audit_mutation()""")

def downgrade():
    op.execute("DROP TABLE guest_claim_audit")
    op.execute("DROP TABLE guest_ticket_claim")
    op.execute("DROP FUNCTION guard_guest_claim()")
