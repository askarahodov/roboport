"""Неизменяемые receipts для двух synthetic create-операций автомобиля.

Revision ID: 0026_auto_create_idempotency
Revises: 0025_auto_intake_delegation
"""
from alembic import op

revision = "0026_auto_create_idempotency"
down_revision = "0025_auto_intake_delegation"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
      CREATE TABLE auto_create_receipt (
        actor_id uuid NOT NULL REFERENCES app_user(id) ON DELETE RESTRICT,
        operation text NOT NULL CHECK (
          operation IN ('vehicle.link.propose','vehicle.intake.grant')
        ),
        key_hash char(64) NOT NULL CHECK (key_hash ~ '^[0-9a-f]{64}$'),
        request_fingerprint char(64) NOT NULL
          CHECK (request_fingerprint ~ '^[0-9a-f]{64}$'),
        object_id uuid NOT NULL,
        result_id uuid NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (actor_id,operation,key_hash)
      )
    """)
    op.execute("""
      CREATE INDEX ix_auto_create_receipt_object ON auto_create_receipt(object_id)
    """)
    op.execute("""
      CREATE TRIGGER auto_create_receipt_immutable
      BEFORE UPDATE OR DELETE ON auto_create_receipt
      FOR EACH ROW EXECUTE FUNCTION reject_audit_mutation()
    """)


def downgrade():
    # Только disposable dev PG: production downgrade с удалением receipts запрещён.
    op.execute("DROP TABLE auto_create_receipt")
