"""Неизменяемые receipts для попыток предъявления и решения auto claim.

Revision ID: 0027_auto_claim_retry
Revises: 0026_auto_create_idempotency
"""
from alembic import op

revision = "0027_auto_claim_retry"
down_revision = "0026_auto_create_idempotency"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
      CREATE TABLE auto_claim_retry_receipt (
        actor_id uuid NOT NULL REFERENCES app_user(id) ON DELETE RESTRICT,
        claim_id uuid NOT NULL REFERENCES auto_vehicle_claim(id) ON DELETE RESTRICT,
        operation text NOT NULL CHECK (operation IN ('redeem','review')),
        key_hash char(64) NOT NULL CHECK (key_hash ~ '^[0-9a-f]{64}$'),
        request_fingerprint char(64) NOT NULL
          CHECK (request_fingerprint ~ '^[0-9a-f]{64}$'),
        outcome text NOT NULL CHECK (
          (operation='redeem' AND outcome IN ('PENDING_REVIEW','REJECTED'))
          OR (operation='review' AND outcome IN ('VERIFIED','DECLINED'))
        ),
        created_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY(actor_id,operation,key_hash)
      )
    """)
    op.execute("""
      CREATE INDEX ix_auto_claim_retry_receipt_claim
      ON auto_claim_retry_receipt(claim_id)
    """)
    op.execute("""
      CREATE TRIGGER auto_claim_retry_receipt_immutable
      BEFORE UPDATE OR DELETE ON auto_claim_retry_receipt
      FOR EACH ROW EXECUTE FUNCTION reject_audit_mutation()
    """)


def downgrade():
    # Удаление только на disposable dev PostgreSQL.
    op.execute("DROP TABLE auto_claim_retry_receipt")
