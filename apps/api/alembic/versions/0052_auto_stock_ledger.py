"""Synthetic automotive stock ledger, internal PG17 only.

Revision ID: 0052_auto_stock_ledger
Revises: 0051_auto_reschedule_idem
"""
from alembic import op

revision = "0052_auto_stock_ledger"
down_revision = "0051_auto_reschedule_idem"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
      CREATE TABLE auto_stock_sku (
        id uuid PRIMARY KEY,
        organization_id uuid NOT NULL REFERENCES auto_organization(id),
        location_id uuid NOT NULL REFERENCES auto_service_location(id),
        article text NOT NULL CHECK (length(btrim(article)) BETWEEN 1 AND 80),
        created_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE (organization_id, location_id, article)
      )
    """)
    op.execute("""
      CREATE FUNCTION guard_auto_stock_sku() RETURNS trigger LANGUAGE plpgsql AS $fn$
      BEGIN
        IF TG_OP <> 'INSERT' THEN
          RAISE EXCEPTION 'stock sku identity is immutable';
        END IF;
        IF NOT EXISTS (
          SELECT 1 FROM auto_service_location l
          JOIN auto_organization o ON o.id=l.organization_id
          WHERE l.id=NEW.location_id AND o.id=NEW.organization_id
            AND l.disabled_at IS NULL AND o.disabled_at IS NULL
          FOR SHARE OF l,o
        ) THEN
          RAISE EXCEPTION 'stock location outside enabled organization';
        END IF;
        RETURN NEW;
      END $fn$
    """)
    op.execute("""
      CREATE TRIGGER auto_stock_sku_guard BEFORE INSERT OR UPDATE OR DELETE
      ON auto_stock_sku FOR EACH ROW EXECUTE FUNCTION guard_auto_stock_sku()
    """)
    op.execute("""
      CREATE TABLE auto_stock_event (
        id uuid PRIMARY KEY,
        request_id uuid NOT NULL UNIQUE,
        sku_id uuid NOT NULL REFERENCES auto_stock_sku(id),
        organization_id uuid NOT NULL REFERENCES auto_organization(id),
        location_id uuid NOT NULL REFERENCES auto_service_location(id),
        action text NOT NULL CHECK (action IN ('RECEIPT','RESERVE','RELEASE')),
        quantity bigint NOT NULL CHECK (quantity BETWEEN 1 AND 1000000000),
        created_at timestamptz NOT NULL DEFAULT now()
      )
    """)
    op.execute("""
      CREATE INDEX ix_auto_stock_event_sku ON auto_stock_event(sku_id)
    """)
    op.execute("""
      CREATE FUNCTION immutable_auto_stock_event() RETURNS trigger LANGUAGE plpgsql AS $fn$
      BEGIN
        RAISE EXCEPTION 'stock event history is immutable';
      END $fn$
    """)
    op.execute("""
      CREATE TRIGGER auto_stock_event_immutable BEFORE UPDATE OR DELETE
      ON auto_stock_event FOR EACH ROW EXECUTE FUNCTION immutable_auto_stock_event()
    """)
    # Events must be inserted through the guarded transaction, not via direct INSERT.
    # DB owner remains privileged; future application DB role must be minimally scoped.
    op.execute("""
      CREATE FUNCTION guard_auto_stock_event_insert() RETURNS trigger LANGUAGE plpgsql AS $fn$
      DECLARE sku record; available_qty bigint; reserved_qty bigint;
      BEGIN
        SELECT organization_id,location_id INTO sku
          FROM auto_stock_sku WHERE id=NEW.sku_id FOR UPDATE;
        IF NOT FOUND OR (NEW.organization_id, NEW.location_id)
                        IS DISTINCT FROM (sku.organization_id,sku.location_id) THEN
          RAISE EXCEPTION 'foreign stock scope';
        END IF;
        SELECT on_hand, reserved INTO available_qty, reserved_qty
          FROM auto_stock_balance WHERE sku_id=NEW.sku_id;
        IF (NEW.action='RESERVE' AND available_qty-reserved_qty < NEW.quantity)
           OR (NEW.action='RELEASE' AND reserved_qty < NEW.quantity) THEN
          RAISE EXCEPTION 'insufficient stock or reservation';
        END IF;
        RETURN NEW;
      END $fn$
    """)
    op.execute("""
      CREATE TRIGGER auto_stock_event_scope BEFORE INSERT
      ON auto_stock_event FOR EACH ROW EXECUTE FUNCTION guard_auto_stock_event_insert()
    """)
    op.execute("""
      CREATE VIEW auto_stock_balance AS
      SELECT sku.id AS sku_id,sku.organization_id,sku.location_id,
             COALESCE(SUM(CASE WHEN e.action='RECEIPT' THEN e.quantity ELSE 0 END),0)::bigint AS on_hand,
             COALESCE(SUM(CASE WHEN e.action='RESERVE' THEN e.quantity
                               WHEN e.action='RELEASE' THEN -e.quantity ELSE 0 END),0)::bigint AS reserved
        FROM auto_stock_sku sku LEFT JOIN auto_stock_event e ON e.sku_id=sku.id
       GROUP BY sku.id,sku.organization_id,sku.location_id
    """)
    op.execute("""
      CREATE FUNCTION record_auto_stock(
        p_sku_id uuid, p_request_id uuid, p_action text, p_quantity bigint
      ) RETURNS uuid LANGUAGE plpgsql AS $fn$
      DECLARE sku record; prior record; balance record; event_id uuid;
      BEGIN
        IF p_sku_id IS NULL OR p_request_id IS NULL
           OR p_action NOT IN ('RECEIPT','RESERVE','RELEASE') OR p_action IS NULL
           OR p_quantity IS NULL OR p_quantity < 1 OR p_quantity > 1000000000 THEN
          RAISE EXCEPTION 'invalid stock operation';
        END IF;
        SELECT s.organization_id,s.location_id INTO sku
          FROM auto_stock_sku s JOIN auto_service_location l
            ON l.id=s.location_id AND l.organization_id=s.organization_id
          JOIN auto_organization o ON o.id=s.organization_id
          WHERE s.id=p_sku_id AND l.disabled_at IS NULL AND o.disabled_at IS NULL
          FOR UPDATE OF s;
        IF NOT FOUND THEN
          RAISE EXCEPTION 'stock sku unavailable';
        END IF;
        SELECT * INTO prior FROM auto_stock_event WHERE request_id=p_request_id;
        IF FOUND THEN
          IF prior.sku_id=p_sku_id AND prior.action=p_action
             AND prior.quantity=p_quantity THEN
            RETURN prior.id;
          END IF;
          RAISE EXCEPTION 'stock request replay mismatch';
        END IF;
        SELECT on_hand,reserved INTO balance FROM auto_stock_balance WHERE sku_id=p_sku_id;
        IF (p_action='RESERVE' AND balance.on_hand-balance.reserved < p_quantity)
           OR (p_action='RELEASE' AND balance.reserved < p_quantity) THEN
          RAISE EXCEPTION 'insufficient stock or reservation';
        END IF;
        event_id := gen_random_uuid();
        INSERT INTO auto_stock_event
          (id,request_id,sku_id,organization_id,location_id,action,quantity)
          VALUES (event_id,p_request_id,p_sku_id,sku.organization_id,sku.location_id,
                  p_action,p_quantity);
        RETURN event_id;
      END $fn$
    """)
    # Future non-owner DB roles require explicit GRANT after security design.
    op.execute("REVOKE ALL ON FUNCTION record_auto_stock(uuid,uuid,text,bigint) FROM PUBLIC")


def downgrade():
    # Disposable synthetic DB only; destroys warehouse event history.
    op.execute("DROP FUNCTION record_auto_stock(uuid,uuid,text,bigint)")
    op.execute("DROP VIEW auto_stock_balance")
    op.execute("DROP TABLE auto_stock_event")
    op.execute("DROP FUNCTION guard_auto_stock_event_insert()")
    op.execute("DROP FUNCTION immutable_auto_stock_event()")
    op.execute("DROP TABLE auto_stock_sku")
    op.execute("DROP FUNCTION guard_auto_stock_sku()")
