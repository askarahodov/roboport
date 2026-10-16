"""Compatibility and locked inventory reservations.

Revision ID: 0015_inventory
Revises: 0014_estimate_decision
"""
from alembic import op

revision="0015_inventory"
down_revision="0014_estimate_decision"
branch_labels=None
depends_on=None

def upgrade():
    op.execute("""CREATE TABLE part_compatibility (
      id uuid PRIMARY KEY,
      part_number text NOT NULL,
      model_variant text NOT NULL,
      region text NOT NULL,
      revision text NOT NULL,
      verified boolean NOT NULL DEFAULT false,
      revoked_at timestamptz,
      UNIQUE(part_number,model_variant,region,revision),
      CHECK (length(trim(part_number))>0 AND length(trim(model_variant))>0
        AND length(trim(region))>0 AND length(trim(revision))>0)
    )""")
    op.execute("""CREATE TABLE stock_reservation (
      id uuid PRIMARY KEY,
      lot_id uuid NOT NULL REFERENCES stock_lot(id) ON DELETE RESTRICT,
      ticket_id uuid NOT NULL REFERENCES service_ticket(id) ON DELETE RESTRICT,
      compatibility_id uuid NOT NULL REFERENCES part_compatibility(id),
      actor_id uuid NOT NULL REFERENCES app_user(id),
      qty bigint NOT NULL CHECK(qty>0),
      status text NOT NULL CHECK(status IN ('ACTIVE','CANCELLED')),
      created_at timestamptz NOT NULL DEFAULT now(),
      cancelled_at timestamptz
    )""")
    op.execute("""CREATE TABLE stock_movement (
      id uuid PRIMARY KEY,
      reservation_id uuid NOT NULL REFERENCES stock_reservation(id),
      lot_id uuid NOT NULL REFERENCES stock_lot(id),
      delta_reserved bigint NOT NULL CHECK(delta_reserved<>0),
      action text NOT NULL CHECK(action IN ('RESERVE','RELEASE')),
      created_at timestamptz NOT NULL DEFAULT now()
    )""")
    op.execute("""CREATE TABLE inventory_receipt (
      actor_id uuid NOT NULL REFERENCES app_user(id),
      request_key text NOT NULL,
      fingerprint text NOT NULL CHECK(length(fingerprint)=64),
      response jsonb NOT NULL,
      PRIMARY KEY(actor_id,request_key)
    )""")
    op.execute("""CREATE FUNCTION reject_inventory_ledger_change() RETURNS trigger
      LANGUAGE plpgsql AS $body$ BEGIN
        RAISE EXCEPTION 'inventory ledger is append-only';
      END $body$""")
    for table in ('stock_movement','inventory_receipt'):
        op.execute(f"""CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE
                   ON {table} FOR EACH ROW EXECUTE FUNCTION reject_inventory_ledger_change()""")

def downgrade():
    op.execute("DROP TABLE inventory_receipt")
    op.execute("DROP TABLE stock_movement")
    op.execute("DROP TABLE stock_reservation")
    op.execute("DROP TABLE part_compatibility")
    op.execute("DROP FUNCTION reject_inventory_ledger_change()")
