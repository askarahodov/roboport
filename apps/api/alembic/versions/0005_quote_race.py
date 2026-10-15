"""Serialize quote versions and decisions on parent ticket.

Revision ID: 0005_quote_race
Revises: 0004_approval
"""
from alembic import op
revision = "0005_quote_race"
down_revision = "0004_approval"
branch_labels = None
depends_on = None

def upgrade():
    op.execute("""
      CREATE FUNCTION guard_estimate_version_insert() RETURNS trigger LANGUAGE plpgsql AS $body$
      BEGIN
        PERFORM 1 FROM service_ticket WHERE id = NEW.ticket_id FOR UPDATE;
        IF NOT FOUND THEN
          RAISE EXCEPTION 'ticket missing';
        END IF;
        IF EXISTS (
          SELECT 1 FROM estimate
          WHERE ticket_id = NEW.ticket_id AND state IN ('APPROVED','DECLINED')
        ) THEN
          RAISE EXCEPTION 'decision finalized: cannot add new quote version';
        END IF;
        RETURN NEW;
      END $body$;
    """)
    op.execute("""
      CREATE TRIGGER estimate_version_ticket_guard BEFORE INSERT ON estimate
      FOR EACH ROW EXECUTE FUNCTION guard_estimate_version_insert()
    """)
    # Both inserts of quotes and approvals now serialize through the same ticket lock.
    # Approval also has a SELECT FOR UPDATE on the estimate row; move parent lock first
    # to avoid lock-order inversions in concurrent workflows.
    op.execute("""
      CREATE OR REPLACE FUNCTION guard_estimate_approval() RETURNS trigger LANGUAGE plpgsql AS $body$
      DECLARE
        quote_ticket uuid;
        quote_state text;
        quote_version integer;
        latest_version integer;
      BEGIN
        SELECT ticket_id INTO quote_ticket FROM estimate WHERE id=NEW.estimate_id;
        IF quote_ticket IS NULL THEN
          RAISE EXCEPTION 'estimate missing';
        END IF;
        PERFORM 1 FROM service_ticket WHERE id=quote_ticket FOR UPDATE;
        SELECT state,version INTO quote_state,quote_version
          FROM estimate WHERE id=NEW.estimate_id FOR UPDATE;
        IF quote_state <> 'SENT' THEN
          RAISE EXCEPTION 'only SENT estimate may be approved';
        END IF;
        SELECT max(version) INTO latest_version FROM estimate WHERE ticket_id=quote_ticket;
        IF quote_version <> latest_version THEN
          RAISE EXCEPTION 'stale estimate version';
        END IF;
        UPDATE estimate SET state=NEW.decision WHERE id=NEW.estimate_id;
        RETURN NEW;
      END $body$;
    """)

def downgrade():
    op.execute("DROP TRIGGER estimate_version_ticket_guard ON estimate")
    op.execute("DROP FUNCTION guard_estimate_version_insert()")
    op.execute("""
      CREATE OR REPLACE FUNCTION guard_estimate_approval() RETURNS trigger LANGUAGE plpgsql AS $body$
      DECLARE
        quote_state text;
        quote_ticket uuid;
        quote_version integer;
        latest_version integer;
      BEGIN
        SELECT e.state,e.ticket_id,e.version
          INTO quote_state,quote_ticket,quote_version
          FROM estimate e WHERE e.id=NEW.estimate_id FOR UPDATE;
        IF NOT FOUND OR quote_state <> 'SENT' THEN
          RAISE EXCEPTION 'only SENT estimate may be approved';
        END IF;
        PERFORM 1 FROM service_ticket WHERE id=quote_ticket FOR UPDATE;
        SELECT max(e.version) INTO latest_version FROM estimate e WHERE e.ticket_id=quote_ticket;
        IF quote_version <> latest_version THEN
          RAISE EXCEPTION 'stale estimate version';
        END IF;
        UPDATE estimate SET state=NEW.decision WHERE id=NEW.estimate_id;
        RETURN NEW;
      END $body$;
    """)
