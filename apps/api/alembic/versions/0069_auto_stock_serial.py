"""Immutable serial provenance bound to a stocked lot, synthetic only.

Revision ID: 0069_auto_stock_serial
Revises: 0068_auto_stock_reconciliation
"""
from alembic import op

revision="0069_auto_stock_serial"
down_revision="0068_auto_stock_reconciliation"
branch_labels=None
depends_on=None


def upgrade():
    op.execute("""CREATE TABLE auto_stock_serial(
      id uuid PRIMARY KEY,
      request_id uuid NOT NULL UNIQUE,
      organization_id uuid NOT NULL REFERENCES auto_organization(id),
      location_id uuid NOT NULL REFERENCES auto_service_location(id),
      sku_id uuid NOT NULL REFERENCES auto_stock_sku(id),
      lot_id uuid NOT NULL REFERENCES auto_stock_lot(id),
      serial_number text NOT NULL
        CHECK(length(btrim(serial_number)) BETWEEN 3 AND 120
          AND serial_number=btrim(serial_number)),
      registered_by uuid NOT NULL REFERENCES app_user(id),
      created_at timestamptz NOT NULL DEFAULT now(),
      UNIQUE(organization_id,sku_id,serial_number)
    )""")
    op.execute("""CREATE TRIGGER auto_stock_serial_immutable
      BEFORE UPDATE OR DELETE ON auto_stock_serial
      FOR EACH ROW EXECUTE FUNCTION immutable_auto_stock_event()""")
    op.execute("""CREATE FUNCTION guard_auto_stock_serial() RETURNS trigger
      LANGUAGE plpgsql AS $fn$
      DECLARE actor uuid; lot_row record; qty bigint;
      BEGIN
        actor:=NULLIF(current_setting('roboport.serial_actor',true),'')::uuid;
        IF actor IS NULL THEN RAISE EXCEPTION 'serial actor required'; END IF;
        SELECT l.sku_id,l.organization_id,l.location_id INTO lot_row
          FROM auto_stock_lot l WHERE l.id=NEW.lot_id;
        IF NOT FOUND OR (lot_row.sku_id,lot_row.organization_id,lot_row.location_id)
             IS DISTINCT FROM (NEW.sku_id,NEW.organization_id,NEW.location_id)
        THEN RAISE EXCEPTION 'serial lot foreign scope'; END IF;
        PERFORM 1 FROM auto_stock_sku WHERE id=NEW.sku_id FOR UPDATE;
        SELECT allocated INTO qty FROM auto_stock_lot_balance
          WHERE lot_id=NEW.lot_id;
        IF (SELECT count(*) FROM auto_stock_serial WHERE lot_id=NEW.lot_id)>=qty
        THEN RAISE EXCEPTION 'serial registrations exceed placed lot stock'; END IF;
        NEW.registered_by:=actor;
        RETURN NEW;
      END $fn$""")
    op.execute("""CREATE TRIGGER auto_stock_serial_actor
      BEFORE INSERT ON auto_stock_serial
      FOR EACH ROW EXECUTE FUNCTION guard_auto_stock_serial()""")
    op.execute("""CREATE FUNCTION register_auto_stock_serial(
      p_lot uuid,p_request uuid,p_serial text
    ) RETURNS uuid LANGUAGE plpgsql AS $fn$
      DECLARE scope record; prior record; result_id uuid;
      BEGIN
        IF p_lot IS NULL OR p_request IS NULL OR p_serial IS NULL
          OR length(p_serial) NOT BETWEEN 3 AND 120
          OR btrim(p_serial)<>p_serial
        THEN RAISE EXCEPTION 'invalid serial'; END IF;
        SELECT l.sku_id,l.organization_id,l.location_id INTO scope
          FROM auto_stock_lot l
          JOIN auto_service_location loc ON loc.id=l.location_id
            AND loc.organization_id=l.organization_id
          JOIN auto_organization org ON org.id=l.organization_id
          WHERE l.id=p_lot AND loc.disabled_at IS NULL
             AND org.disabled_at IS NULL;
        IF NOT FOUND THEN RAISE EXCEPTION 'serial lot unavailable'; END IF;
        PERFORM 1 FROM auto_stock_sku WHERE id=scope.sku_id FOR UPDATE;
        SELECT * INTO prior FROM auto_stock_serial WHERE request_id=p_request;
        IF FOUND THEN
          IF prior.lot_id=p_lot AND prior.serial_number=p_serial AND
            prior.registered_by=
              NULLIF(current_setting('roboport.serial_actor',true),'')::uuid
          THEN RETURN prior.id; END IF;
          RAISE EXCEPTION 'serial request replay mismatch'; END IF;
        result_id:=gen_random_uuid();
        INSERT INTO auto_stock_serial
          (id,request_id,organization_id,location_id,sku_id,lot_id,
           serial_number,registered_by)
        VALUES(result_id,p_request,scope.organization_id,scope.location_id,
          scope.sku_id,p_lot,p_serial,
          NULLIF(current_setting('roboport.serial_actor',true),'')::uuid);
        RETURN result_id;
      END $fn$""")
    op.execute("REVOKE ALL ON auto_stock_serial FROM PUBLIC")
    op.execute("REVOKE ALL ON FUNCTION register_auto_stock_serial(uuid,uuid,text) FROM PUBLIC")


def downgrade():
    op.execute("DROP FUNCTION register_auto_stock_serial(uuid,uuid,text)")
    op.execute("DROP TABLE auto_stock_serial")
    op.execute("DROP FUNCTION guard_auto_stock_serial()")
