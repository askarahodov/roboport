"""Extend independent synthetic CRM consent and outbox to MESSENGER."""
from alembic import op
from sqlalchemy import text

revision="0140_auto_crm_mock_messenger"
down_revision="0139_auto_crm_survey"
branch_labels=None
depends_on=None

TABLES=("auto_crm_contact_consent_event","auto_crm_mock_outbox")


def _replace(table,channels):
    constraint=table+"_channel_code_check"
    op.execute(f"ALTER TABLE {table} DROP CONSTRAINT {constraint}")
    op.execute(f"ALTER TABLE {table} ADD CONSTRAINT {constraint} CHECK(channel_code IN ({channels}))")


def upgrade():
    for table in TABLES:
        _replace(table,"'CALL','EMAIL','SMS','MESSENGER'")


def downgrade():
    bind=op.get_bind()
    # Do not erase immutable channel-specific evidence to permit downgrade.
    for table in TABLES:
        if bind.execute(text(f"SELECT 1 FROM {table} WHERE channel_code='MESSENGER' LIMIT 1")).first():
            raise RuntimeError("Messenger evidence exists: restore isolated synthetic backup")
    for table in TABLES:
        _replace(table,"'CALL','EMAIL','SMS'")
