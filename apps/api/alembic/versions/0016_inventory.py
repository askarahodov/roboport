"""Compatibility and locked inventory reservations.

Revision ID: 0016_inventory
Revises: 0014_estimate_decision
"""
from alembic import op

revision="0016_inventory"
down_revision="0015_catalog"
branch_labels=None
depends_on=None

def upgrade():
    op.execute("""ALTER TABLE robot_device
      ADD COLUMN model_variant text, ADD COLUMN region text, ADD COLUMN revision text""")
    op.execute("ALTER TABLE acl_entry DROP CONSTRAINT acl_entry_action_check")
    op.execute("""ALTER TABLE acl_entry ADD CONSTRAINT acl_entry_action_check CHECK
      (action IN ('ticket.read','ticket.diagnose','ticket.intake','ticket.manage',
                  'estimate.read','estimate.approve','stock.reserve'))""")
    op.execute("""CREATE TABLE inventory_part (
      id uuid PRIMARY KEY,
      part_number text NOT NULL UNIQUE CHECK(length(trim(part_number))>0)
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
      cancelled_at timestamptz,
      CONSTRAINT stock_reservation_state_consistent CHECK (
        (status='ACTIVE' AND cancelled_at IS NULL) OR
        (status='CANCELLED' AND cancelled_at IS NOT NULL))
    )""")
    op.execute("""CREATE FUNCTION guard_reservation_update() RETURNS trigger
      LANGUAGE plpgsql AS $body$
      BEGIN
        IF ROW(NEW.id,NEW.lot_id,NEW.ticket_id,NEW.compatibility_id,NEW.actor_id,
               NEW.qty,NEW.created_at) IS DISTINCT FROM
           ROW(OLD.id,OLD.lot_id,OLD.ticket_id,OLD.compatibility_id,OLD.actor_id,
               OLD.qty,OLD.created_at) THEN
          RAISE EXCEPTION 'reservation identity immutable';
        END IF;
        IF OLD.status='CANCELLED' OR
           NEW.status NOT IN ('ACTIVE','CANCELLED') OR
           (OLD.status='ACTIVE' AND NEW.status='ACTIVE' AND
            NEW.cancelled_at IS DISTINCT FROM OLD.cancelled_at) OR
           (NEW.status='CANCELLED' AND (OLD.status<>'ACTIVE' OR NEW.cancelled_at IS NULL)) THEN
          RAISE EXCEPTION 'reservation status transition forbidden';
        END IF;
        RETURN NEW;
      END $body$""")
    op.execute("""CREATE TRIGGER reservation_state_guard BEFORE UPDATE ON stock_reservation
      FOR EACH ROW EXECUTE FUNCTION guard_reservation_update()""")
    op.execute("""CREATE FUNCTION reject_reservation_delete() RETURNS trigger LANGUAGE plpgsql
      AS $body$ BEGIN RAISE EXCEPTION 'reservation deletion forbidden'; END $body$""")
    op.execute("""CREATE TRIGGER reservation_no_delete BEFORE DELETE ON stock_reservation
      FOR EACH ROW EXECUTE FUNCTION reject_reservation_delete()""")
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
    op.execute("DELETE FROM acl_entry WHERE action='stock.reserve'")
    op.execute("ALTER TABLE acl_entry DROP CONSTRAINT acl_entry_action_check")
    op.execute("""ALTER TABLE acl_entry ADD CONSTRAINT acl_entry_action_check CHECK
      (action IN ('ticket.read','ticket.diagnose','ticket.intake','ticket.manage',
                  'estimate.read','estimate.approve'))""")
    op.execute("ALTER TABLE robot_device DROP COLUMN model_variant, DROP COLUMN region, DROP COLUMN revision")
    op.execute("DROP TABLE inventory_receipt")
    op.execute("DROP TABLE stock_movement")
    op.execute("DROP TABLE stock_reservation")
    op.execute("DROP FUNCTION guard_reservation_update()")
    op.execute("DROP FUNCTION reject_reservation_delete()")
    op.execute("DROP TABLE inventory_part")
    op.execute("DROP FUNCTION reject_inventory_ledger_change()")
