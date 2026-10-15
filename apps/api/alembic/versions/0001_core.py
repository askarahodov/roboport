"""Synthetic customer/ticket invariant baseline; deliberately minimal.

Revision ID: 0001_core
Revises:
"""
from alembic import op

revision = "0001_core"
down_revision = None
branch_labels = None
depends_on = None

STATES = (
    "DRAFT", "SUBMITTED", "TRIAGE", "AWAITING_DELIVERY", "RECEIVED",
    "DIAGNOSING", "SAFETY_HOLD", "ESTIMATE_DRAFT", "AWAITING_APPROVAL",
    "APPROVED", "DECLINED", "AWAITING_PARTS", "REPAIRING", "QC_PENDING",
    "QC_FAILED", "READY_FOR_HANDOVER", "COMPLETED", "CANCELLED",
    "UNREPAIRABLE", "WARRANTY_ROUTE",
)


def upgrade():
    op.execute("""
        CREATE TABLE customer (
          id uuid PRIMARY KEY,
          label text NOT NULL CHECK (length(trim(label)) > 0)
        )
    """)
    op.execute("""
        CREATE TABLE robot_device (
          id uuid PRIMARY KEY,
          serial_number text UNIQUE
        )
    """)
    op.execute("""
        CREATE TABLE service_ticket (
          id uuid PRIMARY KEY,
          customer_id uuid NOT NULL REFERENCES customer(id) ON DELETE RESTRICT,
          device_id uuid REFERENCES robot_device(id) ON DELETE RESTRICT,
          state text NOT NULL DEFAULT 'DRAFT',
          version integer NOT NULL DEFAULT 1 CHECK (version > 0),
          created_at timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT ck_ticket_state CHECK (state IN (""" + ",".join("'" + s + "'" for s in STATES) + """))
        )
    """)
    op.execute("CREATE INDEX ix_service_ticket_customer ON service_ticket(customer_id)")
    op.execute("""
        CREATE TABLE ticket_state_event (
          id uuid PRIMARY KEY,
          ticket_id uuid NOT NULL REFERENCES service_ticket(id) ON DELETE RESTRICT,
          from_state text NOT NULL,
          to_state text NOT NULL,
          expected_version integer NOT NULL CHECK (expected_version > 0),
          created_at timestamptz NOT NULL DEFAULT now(),
          CONSTRAINT ck_ticket_event_from CHECK (from_state IN (""" + ",".join("'" + s + "'" for s in STATES) + """)),
          CONSTRAINT ck_ticket_event_to CHECK (to_state IN (""" + ",".join("'" + s + "'" for s in STATES) + """))
        )
    """)
    op.execute("""
        CREATE TABLE audit_event (
          id uuid PRIMARY KEY,
          ticket_id uuid REFERENCES service_ticket(id) ON DELETE RESTRICT,
          action text NOT NULL CHECK (length(action) > 0),
          created_at timestamptz NOT NULL DEFAULT now()
        )
    """)


def downgrade():
    op.execute("DROP TABLE audit_event")
    op.execute("DROP TABLE ticket_state_event")
    op.execute("DROP TABLE service_ticket")
    op.execute("DROP TABLE robot_device")
    op.execute("DROP TABLE customer")
