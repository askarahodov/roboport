"""Durable estimate decision receipts tied to immutable approvals.

Revision ID: 0014_estimate_decision
Revises: 0013_ticket_idempotency
"""
from alembic import op
revision="0014_estimate_decision"
down_revision="0013_ticket_idempotency"
branch_labels=None
depends_on=None

def upgrade():
    op.execute("""CREATE TABLE estimate_decision_receipt (
      actor_id uuid NOT NULL REFERENCES app_user(id) ON DELETE RESTRICT,
      estimate_id uuid NOT NULL REFERENCES estimate(id) ON DELETE RESTRICT,
      request_key text NOT NULL CHECK(length(request_key) BETWEEN 1 AND 128),
      fingerprint text NOT NULL CHECK(length(fingerprint)=64),
      response jsonb NOT NULL,
      created_at timestamptz NOT NULL DEFAULT now(),
      PRIMARY KEY(actor_id,request_key)
    )""")
    op.execute("""CREATE TABLE estimate_staff_receipt (
      actor_id uuid NOT NULL REFERENCES app_user(id),
      request_key text NOT NULL,
      operation text NOT NULL,
      fingerprint text NOT NULL,
      response jsonb NOT NULL,
      PRIMARY KEY(actor_id,request_key)
    )""")
    op.execute("""CREATE FUNCTION reject_estimate_receipt_mutation() RETURNS trigger
      LANGUAGE plpgsql AS $body$ BEGIN
        RAISE EXCEPTION 'estimate decision receipt immutable';
      END $body$""")
    op.execute("""CREATE TRIGGER estimate_receipt_immutable
      BEFORE UPDATE OR DELETE ON estimate_decision_receipt
      FOR EACH ROW EXECUTE FUNCTION reject_estimate_receipt_mutation()""")

def downgrade():
    op.execute("DROP TABLE estimate_staff_receipt")
    op.execute("DROP TABLE estimate_decision_receipt")
    op.execute("DROP FUNCTION reject_estimate_receipt_mutation()")
