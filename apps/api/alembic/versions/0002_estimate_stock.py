"""Synthetic estimate and inventory constraints.

Revision ID: 0002_estimate_stock
Revises: 0001_core
"""
from alembic import op

revision = "0002_estimate_stock"
down_revision = "0001_core"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
      CREATE TABLE estimate (
        id uuid PRIMARY KEY,
        ticket_id uuid NOT NULL REFERENCES service_ticket(id) ON DELETE RESTRICT,
        version integer NOT NULL CHECK (version > 0),
        state text NOT NULL DEFAULT 'DRAFT'
          CHECK (state IN ('DRAFT','SENT','APPROVED','DECLINED')),
        total_minor bigint NOT NULL CHECK (total_minor >= 0),
        currency char(3) NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE(ticket_id, version)
      )
    """)
    op.execute("""
      CREATE TABLE estimate_line (
        id uuid PRIMARY KEY,
        estimate_id uuid NOT NULL REFERENCES estimate(id) ON DELETE RESTRICT,
        description text NOT NULL CHECK (length(trim(description)) > 0),
        amount_minor bigint NOT NULL CHECK (amount_minor >= 0)
      )
    """)
    op.execute("""
      CREATE TABLE stock_lot (
        id uuid PRIMARY KEY,
        part_number text NOT NULL CHECK (length(trim(part_number)) > 0),
        available_qty bigint NOT NULL CHECK (available_qty >= 0),
        reserved_qty bigint NOT NULL DEFAULT 0 CHECK (reserved_qty >= 0),
        CHECK (reserved_qty <= available_qty)
      )
    """)
    op.execute("""
      CREATE FUNCTION prevent_frozen_estimate_change() RETURNS trigger LANGUAGE plpgsql AS $body$
      BEGIN
        IF EXISTS (
          SELECT 1 FROM estimate e
          WHERE e.id IN (NEW.estimate_id, OLD.estimate_id)
            AND e.state <> 'DRAFT'
        ) THEN
          RAISE EXCEPTION 'estimate lines frozen';
        END IF;
        RETURN COALESCE(NEW, OLD);
      END $body$;
    """)
    op.execute("""
      CREATE TRIGGER estimate_line_freeze
      BEFORE INSERT OR UPDATE OR DELETE ON estimate_line
      FOR EACH ROW EXECUTE FUNCTION prevent_frozen_estimate_change()
    """)
    op.execute("""
      CREATE FUNCTION prevent_frozen_estimate_header() RETURNS trigger LANGUAGE plpgsql AS $body$
      BEGIN
        IF OLD.state <> 'DRAFT' AND NEW.state NOT IN ('APPROVED', 'DECLINED') THEN
          RAISE EXCEPTION 'estimate cannot return to draft';
        END IF;
        IF OLD.state IN ('APPROVED', 'DECLINED') AND NEW.state IS DISTINCT FROM OLD.state THEN
          RAISE EXCEPTION 'estimate decision is terminal';
        END IF;
        IF OLD.state <> 'DRAFT' AND
           (NEW.total_minor IS DISTINCT FROM OLD.total_minor OR
            NEW.currency IS DISTINCT FROM OLD.currency OR
            NEW.ticket_id IS DISTINCT FROM OLD.ticket_id OR
            NEW.version IS DISTINCT FROM OLD.version) THEN
          RAISE EXCEPTION 'estimate header frozen';
        END IF;
        RETURN NEW;
      END $body$;
    """)
    op.execute("""
      CREATE TRIGGER estimate_header_freeze BEFORE UPDATE ON estimate
      FOR EACH ROW EXECUTE FUNCTION prevent_frozen_estimate_header()
    """)


def downgrade():
    op.execute("DROP TABLE stock_lot")
    op.execute("DROP TABLE estimate_line")
    op.execute("DROP TABLE estimate")
    op.execute("DROP FUNCTION prevent_frozen_estimate_change()")
    op.execute("DROP FUNCTION prevent_frozen_estimate_header()")
