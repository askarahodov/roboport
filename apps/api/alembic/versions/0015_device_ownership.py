"""Immutable owner history and one active owner per device.

Revision ID: 0015_device_ownership
Revises: 0014_estimate_decision
"""
from alembic import op

revision="0015_device_ownership"
down_revision="0014_estimate_decision"
branch_labels=None
depends_on=None


def upgrade():
    op.execute("""CREATE TABLE device_ownership (
      id uuid PRIMARY KEY,
      device_id uuid NOT NULL REFERENCES robot_device(id) ON DELETE RESTRICT,
      customer_id uuid NOT NULL REFERENCES customer(id) ON DELETE RESTRICT,
      started_at timestamptz NOT NULL DEFAULT now(),
      ended_at timestamptz,
      CONSTRAINT ck_owner_interval CHECK(ended_at IS NULL OR ended_at>started_at)
    )""")
    op.execute("""CREATE UNIQUE INDEX uq_device_one_active_owner
      ON device_ownership(device_id) WHERE ended_at IS NULL""")
    op.execute("""CREATE INDEX ix_device_owner_history
      ON device_ownership(device_id,started_at DESC)""")
    op.execute("""CREATE FUNCTION protect_device_ownership() RETURNS trigger LANGUAGE plpgsql AS $$
      BEGIN
        IF TG_OP='DELETE' THEN
          RAISE EXCEPTION 'owner history append-only';
        END IF;
        IF (NEW.id,NEW.device_id,NEW.customer_id,NEW.started_at) IS DISTINCT FROM
           (OLD.id,OLD.device_id,OLD.customer_id,OLD.started_at) THEN
          RAISE EXCEPTION 'owner identity immutable';
        END IF;
        IF OLD.ended_at IS NOT NULL AND NEW.ended_at IS DISTINCT FROM OLD.ended_at THEN
          RAISE EXCEPTION 'owner history cannot reopen';
        END IF;
        RETURN NEW;
      END $$""")
    op.execute("""CREATE TRIGGER device_ownership_guard BEFORE UPDATE OR DELETE
      ON device_ownership FOR EACH ROW EXECUTE FUNCTION protect_device_ownership()""")


def downgrade():
    op.execute("DROP TABLE device_ownership")
    op.execute("DROP FUNCTION protect_device_ownership()")
