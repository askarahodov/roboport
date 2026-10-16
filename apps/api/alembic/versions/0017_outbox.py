"""Transactional order/estimate outbox and synthetic delivery ledger.

Revision ID: 0017_outbox
Revises: 0016_inventory
"""
from alembic import op

revision="0017_outbox"
down_revision="0016_inventory"
branch_labels=None
depends_on=None

def upgrade():
    op.execute("""CREATE TABLE notification_outbox (
      id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
      event_key text NOT NULL UNIQUE CHECK(length(event_key) BETWEEN 1 AND 255),
      aggregate_type text NOT NULL CHECK(aggregate_type IN ('ticket','estimate')),
      aggregate_id uuid NOT NULL,
      event_type text NOT NULL CHECK(event_type IN ('CREATED','STATE_CHANGED')),
      payload jsonb NOT NULL CHECK(jsonb_typeof(payload)='object'),
      status text NOT NULL DEFAULT 'PENDING'
        CHECK(status IN ('PENDING','PROCESSING','DELIVERED','DEAD')),
      attempts integer NOT NULL DEFAULT 0 CHECK(attempts BETWEEN 0 AND 5),
      available_at timestamptz NOT NULL DEFAULT now(),
      claimed_by text,
      claimed_at timestamptz,
      last_error text,
      created_at timestamptz NOT NULL DEFAULT now(),
      delivered_at timestamptz,
      CHECK (
        (status='PROCESSING' AND claimed_by IS NOT NULL AND claimed_at IS NOT NULL)
        OR (status<>'PROCESSING' AND claimed_by IS NULL AND claimed_at IS NULL)
      ),
      CHECK ((status='DELIVERED')=(delivered_at IS NOT NULL))
    )""")
    op.execute("""CREATE INDEX ix_outbox_claim_ready
      ON notification_outbox(available_at,id)
      WHERE status IN ('PENDING','PROCESSING')""")
    op.execute("""CREATE TABLE synthetic_notification_delivery (
      outbox_id uuid PRIMARY KEY REFERENCES notification_outbox(id),
      channel text NOT NULL DEFAULT 'DEV_SINK' CHECK(channel='DEV_SINK'),
      delivered_at timestamptz NOT NULL DEFAULT now()
    )""")
    op.execute("""CREATE FUNCTION reject_delivery_mutation() RETURNS trigger LANGUAGE plpgsql
      AS $body$ BEGIN RAISE EXCEPTION 'synthetic delivery is append-only'; END $body$""")
    op.execute("""CREATE TRIGGER synthetic_delivery_immutable
      BEFORE UPDATE OR DELETE ON synthetic_notification_delivery
      FOR EACH ROW EXECUTE FUNCTION reject_delivery_mutation()""")
    op.execute("""CREATE FUNCTION enqueue_ticket_event() RETURNS trigger LANGUAGE plpgsql AS $body$
      BEGIN
        IF TG_OP='INSERT' OR NEW.state IS DISTINCT FROM OLD.state THEN
          INSERT INTO notification_outbox(event_key,aggregate_type,aggregate_id,event_type,payload)
            VALUES('ticket:' || NEW.id::text || ':' || NEW.version::text || ':' || NEW.state,
                   'ticket',NEW.id,CASE WHEN TG_OP='INSERT' THEN 'CREATED' ELSE 'STATE_CHANGED' END,
                   jsonb_build_object('state',NEW.state,'version',NEW.version))
            ON CONFLICT(event_key) DO NOTHING;
        END IF;
        RETURN NEW;
      END $body$""")
    op.execute("""CREATE TRIGGER ticket_outbox_insert AFTER INSERT ON service_ticket
      FOR EACH ROW EXECUTE FUNCTION enqueue_ticket_event()""")
    op.execute("""CREATE TRIGGER ticket_outbox_update AFTER UPDATE OF state ON service_ticket
      FOR EACH ROW EXECUTE FUNCTION enqueue_ticket_event()""")
    op.execute("""CREATE FUNCTION enqueue_estimate_event() RETURNS trigger LANGUAGE plpgsql AS $body$
      BEGIN
        IF TG_OP='INSERT' OR NEW.state IS DISTINCT FROM OLD.state THEN
          INSERT INTO notification_outbox(event_key,aggregate_type,aggregate_id,event_type,payload)
            VALUES('estimate:' || NEW.id::text || ':' || NEW.version::text || ':' || NEW.state,
                   'estimate',NEW.id,CASE WHEN TG_OP='INSERT' THEN 'CREATED' ELSE 'STATE_CHANGED' END,
                   jsonb_build_object('state',NEW.state,'version',NEW.version))
            ON CONFLICT(event_key) DO NOTHING;
        END IF;
        RETURN NEW;
      END $body$""")
    op.execute("""CREATE TRIGGER estimate_outbox_insert AFTER INSERT ON estimate
      FOR EACH ROW EXECUTE FUNCTION enqueue_estimate_event()""")
    op.execute("""CREATE TRIGGER estimate_outbox_update AFTER UPDATE OF state ON estimate
      FOR EACH ROW EXECUTE FUNCTION enqueue_estimate_event()""")

def downgrade():
    op.execute("DROP TRIGGER estimate_outbox_update ON estimate")
    op.execute("DROP TRIGGER estimate_outbox_insert ON estimate")
    op.execute("DROP TRIGGER ticket_outbox_update ON service_ticket")
    op.execute("DROP TRIGGER ticket_outbox_insert ON service_ticket")
    op.execute("DROP FUNCTION enqueue_estimate_event()")
    op.execute("DROP FUNCTION enqueue_ticket_event()")
    op.execute("DROP TABLE synthetic_notification_delivery")
    op.execute("DROP TABLE notification_outbox")
    op.execute("DROP FUNCTION reject_delivery_mutation()")
