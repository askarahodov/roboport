"""Atomic ticket mutation receipts.

Revision ID: 0013_ticket_idempotency
Revises: 0012_safety_review
"""
from alembic import op

revision="0013_ticket_idempotency"
down_revision="0012_safety_review"
branch_labels=None
depends_on=None


def upgrade():
    op.execute("""CREATE TABLE ticket_mutation_receipt (
        actor_id uuid NOT NULL REFERENCES app_user(id) ON DELETE RESTRICT,
        ticket_id uuid NOT NULL REFERENCES service_ticket(id) ON DELETE RESTRICT,
        operation text NOT NULL CHECK (operation IN ('transition','review','release')),
        request_key text NOT NULL CHECK (length(request_key) BETWEEN 1 AND 128),
        fingerprint text NOT NULL,
        response jsonb NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (actor_id,ticket_id,operation,request_key)
    )""")


def downgrade():
    op.execute("DROP TABLE ticket_mutation_receipt")
