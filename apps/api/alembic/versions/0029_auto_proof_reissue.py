"""Однократная замена утерянного proof без изменения неизменяемого claim.

Revision ID: 0029_auto_proof_reissue
Revises: 0028_auto_proof_throttle
"""
from alembic import op

revision = "0029_auto_proof_reissue"
down_revision = "0028_auto_proof_throttle"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
      CREATE TABLE auto_vehicle_claim_reissue (
        claim_id uuid PRIMARY KEY REFERENCES auto_vehicle_claim(id) ON DELETE RESTRICT,
        issued_by uuid NOT NULL REFERENCES app_user(id) ON DELETE RESTRICT,
        proof_hash char(64) NOT NULL CHECK (proof_hash ~ '^[0-9a-f]{64}$'),
        proof_expires_at timestamptz NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(),
        CHECK (proof_expires_at > created_at)
      )
    """)
    op.execute("""
      CREATE TRIGGER auto_vehicle_claim_reissue_immutable
      BEFORE UPDATE OR DELETE ON auto_vehicle_claim_reissue
      FOR EACH ROW EXECUTE FUNCTION reject_audit_mutation()
    """)
    # Старый audit остается append-only; единственный новый тип REISSUED
    # документирует замену секрета, не заменяя исходный ISSUED.
    op.execute("""
      ALTER TABLE auto_vehicle_claim_audit
      DROP CONSTRAINT auto_vehicle_claim_audit_action_check
    """)
    op.execute("""
      ALTER TABLE auto_vehicle_claim_audit
      ADD CONSTRAINT auto_vehicle_claim_audit_action_check
      CHECK (action IN (
        'ISSUED','REISSUED','PROOF_ACCEPTED','PROOF_REJECTED',
        'VERIFIED','DECLINED','REVOKED'
      ))
    """)


def downgrade():
    # Только disposable dev. Исторический REISSUED остаётся в append-only
    # audit и должен оставаться допустимым CHECK даже после отката.
    # Нельзя перестраивать constraint с более узким списком значений:
    # PostgreSQL отвергнет его, если уже есть REISSUED.
    op.execute("DROP TABLE auto_vehicle_claim_reissue")
