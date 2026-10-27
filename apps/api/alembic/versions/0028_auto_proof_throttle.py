"""Серверный лимит issue/redeem по аутентифицированному пользователю.

Revision ID: 0028_auto_proof_throttle
Revises: 0027_auto_claim_retry
"""
from alembic import op

revision = "0028_auto_proof_throttle"
down_revision = "0027_auto_claim_retry"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
      CREATE TABLE auto_proof_throttle (
        actor_id uuid NOT NULL REFERENCES app_user(id) ON DELETE RESTRICT,
        operation text NOT NULL CHECK(operation IN ('ISSUE','REDEEM')),
        attempts integer NOT NULL CHECK(attempts BETWEEN 1 AND 1000000),
        window_started_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY(actor_id,operation)
      )
    """)


def downgrade():
    # Только для одноразового синтетического стенда.
    op.execute("DROP TABLE auto_proof_throttle")
