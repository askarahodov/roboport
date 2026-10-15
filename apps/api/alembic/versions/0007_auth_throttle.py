"""Persistent lockout for synthetic localhost login.

Revision ID: 0007_auth_throttle
Revises: 0006_local_identity
"""
from alembic import op
revision="0007_auth_throttle"
down_revision="0006_local_identity"
branch_labels=None
depends_on=None

def upgrade():
    op.execute("""
      CREATE TABLE auth_login_throttle(
        login_hash char(64) PRIMARY KEY CHECK (login_hash ~ '^[0-9a-f]{64}$'),
        failures integer NOT NULL DEFAULT 0 CHECK (failures>=0),
        window_started_at timestamptz NOT NULL DEFAULT now()
      )
    """)

def downgrade():
    op.execute("DROP TABLE auth_login_throttle")
