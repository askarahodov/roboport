"""Synthetic direct-peer auto POST limiter, no raw IP stored.

Revision ID: 0031_auto_peer_throttle
Revises: 0030_auto_location_scope

No production trusted ingress, public IP quota or PII.
"""
from alembic import op

revision = "0031_auto_peer_throttle"
down_revision = "0030_auto_location_scope"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
      CREATE TABLE auto_peer_throttle (
        peer_hmac char(64) PRIMARY KEY
          CHECK (peer_hmac ~ '^[0-9a-f]{64}$'),
        attempts integer NOT NULL CHECK (attempts BETWEEN 1 AND 31),
        window_started_at timestamptz NOT NULL DEFAULT now()
      )
    """)


def downgrade():
    # Disposable synthetic DB only; reverting a live quota resets its budget.
    op.execute("DROP TABLE auto_peer_throttle")
