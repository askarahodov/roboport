"""Immutable per-serial issue, return and interbranch movement journal.

Revision ID: 0071_auto_serial_movement
Revises: 0070_auto_stock_reclassification
"""
from alembic import op

revision="0071_auto_serial_movement"
down_revision="0070_auto_stock_reclassification"
branch_labels=None
depends_on=None


def upgrade():
    op.execute("""CREATE TABLE auto_stock_serial_movement (
      id uuid PRIMARY KEY,
      movement_sequence bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
      request_id uuid NOT NULL UNIQUE,
      serial_id uuid NOT NULL REFERENCES auto_stock_serial(id),
      action text NOT NULL CHECK(action IN ('ISSUE','RETURN','TRANSFER')),
      source_lot_id uuid NOT NULL REFERENCES auto_stock_lot(id),
      destination_lot_id uuid REFERENCES auto_stock_lot(id),
      work_order_id uuid REFERENCES auto_work_order(id),
      issue_id uuid UNIQUE REFERENCES auto_stock_lot_work_order_issue(id),
      return_id uuid UNIQUE REFERENCES auto_stock_lot_work_order_return(id),
      interbranch_id uuid UNIQUE REFERENCES auto_stock_interbranch_transfer(id),
      performed_by uuid NOT NULL REFERENCES app_user(id),
      created_at timestamptz NOT NULL DEFAULT now(),
      CHECK((action='ISSUE' AND destination_lot_id IS NULL AND
             work_order_id IS NOT NULL AND issue_id IS NOT NULL AND
             return_id IS NULL AND interbranch_id IS NULL)
         OR (action='RETURN' AND destination_lot_id IS NULL AND
             work_order_id IS NOT NULL AND return_id IS NOT NULL AND
             issue_id IS NULL AND interbranch_id IS NULL)
         OR (action='TRANSFER' AND destination_lot_id IS NOT NULL AND
             destination_lot_id<>source_lot_id AND work_order_id IS NULL
             AND interbranch_id IS NOT NULL AND issue_id IS NULL AND return_id IS NULL))
    )""")
    op.execute("""CREATE TRIGGER auto_stock_serial_movement_immutable
      BEFORE UPDATE OR DELETE ON auto_stock_serial_movement
      FOR EACH ROW EXECUTE FUNCTION immutable_auto_stock_event()""")
    op.execute("""CREATE VIEW auto_stock_serial_current AS
      SELECT s.id AS serial_id,s.organization_id,s.serial_number,s.registered_by,
             l.id AS lot_id,l.sku_id,l.location_id,
             CASE WHEN m.action='ISSUE' THEN 'ISSUED' ELSE 'IN_STOCK' END AS state,
             CASE WHEN m.action='ISSUE' THEN m.work_order_id ELSE NULL END AS work_order_id,
             m.id AS last_movement_id
      FROM auto_stock_serial s
      LEFT JOIN LATERAL (
        SELECT e.* FROM auto_stock_serial_movement e WHERE e.serial_id=s.id
        ORDER BY e.movement_sequence DESC LIMIT 1
      ) m ON true
      JOIN auto_stock_lot l
        ON l.id=COALESCE(m.destination_lot_id,m.source_lot_id,s.lot_id)""")
    # Registration must count CURRENT inventory, including serials transferred
    # from another branch, rather than their immutable original registration lot.
    op.execute("""CREATE OR REPLACE FUNCTION guard_auto_stock_serial()
      RETURNS trigger LANGUAGE plpgsql AS $fn$
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
        SELECT allocated INTO qty FROM auto_stock_lot_balance WHERE lot_id=NEW.lot_id;
        IF (SELECT count(*) FROM auto_stock_serial_current
            WHERE lot_id=NEW.lot_id AND state='IN_STOCK')>=COALESCE(qty,0)
        THEN RAISE EXCEPTION 'serial registrations exceed placed lot stock'; END IF;
        IF EXISTS(
          SELECT 1 FROM auto_stock_serial_current
          WHERE sku_id=NEW.sku_id AND organization_id=NEW.organization_id
            AND serial_number=NEW.serial_number
        ) THEN RAISE EXCEPTION 'serial number already present in current SKU'; END IF;
        NEW.registered_by:=actor;
        RETURN NEW;
      END $fn$""")
    op.execute("""CREATE FUNCTION guard_auto_stock_serial_movement()
      RETURNS trigger LANGUAGE plpgsql AS $fn$
      DECLARE actor uuid; state_row record; src record; dst record; linked record;
      BEGIN
        actor:=NULLIF(current_setting('roboport.serial_actor',true),'')::uuid;
        IF actor IS NULL THEN RAISE EXCEPTION 'serial movement actor required'; END IF;
        SELECT * INTO state_row FROM auto_stock_serial_current
          WHERE serial_id=NEW.serial_id;
        IF NOT FOUND THEN RAISE EXCEPTION 'serial identity unavailable'; END IF;
        SELECT sku_id,organization_id,location_id INTO src FROM auto_stock_lot
          WHERE id=NEW.source_lot_id;
        IF NEW.action='TRANSFER' THEN
          SELECT sku_id,organization_id,location_id INTO dst FROM auto_stock_lot
             WHERE id=NEW.destination_lot_id;
          IF NOT FOUND THEN RAISE EXCEPTION 'serial destination unavailable'; END IF;
          PERFORM 1 FROM auto_stock_sku
             WHERE id IN (src.sku_id,dst.sku_id) ORDER BY id FOR UPDATE;
        ELSE
          PERFORM 1 FROM auto_stock_sku WHERE id=src.sku_id FOR UPDATE;
        END IF;
        SELECT * INTO state_row FROM auto_stock_serial_current
           WHERE serial_id=NEW.serial_id;
        IF NOT FOUND OR state_row.lot_id<>NEW.source_lot_id THEN
          RAISE EXCEPTION 'serial source mismatch'; END IF;
        IF NEW.action='ISSUE' THEN
          IF state_row.state<>'IN_STOCK' THEN RAISE EXCEPTION 'serial already issued'; END IF;
          PERFORM 1 FROM auto_stock_lot_work_order_issue i
          WHERE i.id=NEW.issue_id AND i.lot_id=NEW.source_lot_id
            AND i.work_order_id=NEW.work_order_id AND i.quantity=1;
          IF NOT FOUND THEN RAISE EXCEPTION 'serial issue missing physical ledger'; END IF;
        ELSIF NEW.action='RETURN' THEN
          IF state_row.state<>'ISSUED' OR state_row.work_order_id<>NEW.work_order_id
          THEN RAISE EXCEPTION 'serial return has no matching issue'; END IF;
          PERFORM 1 FROM auto_stock_lot_work_order_return e
          WHERE e.id=NEW.return_id AND e.lot_id=NEW.source_lot_id
            AND e.work_order_id=NEW.work_order_id AND e.quantity=1;
          IF NOT FOUND THEN RAISE EXCEPTION 'serial return missing physical ledger'; END IF;
        ELSE
          IF state_row.state<>'IN_STOCK'
             OR src.organization_id<>dst.organization_id
             OR src.location_id=dst.location_id
             OR (SELECT article FROM auto_stock_sku WHERE id=src.sku_id)
                IS DISTINCT FROM (SELECT article FROM auto_stock_sku WHERE id=dst.sku_id)
          THEN RAISE EXCEPTION 'serial interbranch scope mismatch'; END IF;
          PERFORM 1 FROM auto_stock_interbranch_transfer t
          WHERE t.id=NEW.interbranch_id AND t.source_lot_id=NEW.source_lot_id
            AND t.destination_lot_id=NEW.destination_lot_id AND t.quantity=1;
          IF NOT FOUND THEN RAISE EXCEPTION 'serial transfer missing physical ledger'; END IF;
          IF EXISTS(
            SELECT 1 FROM auto_stock_serial_current other
            WHERE other.serial_id<>NEW.serial_id AND other.organization_id=src.organization_id
              AND other.sku_id=dst.sku_id
              AND other.serial_number=state_row.serial_number
          ) THEN RAISE EXCEPTION 'serial duplicate at destination'; END IF;
        END IF;
        NEW.performed_by:=actor;
        RETURN NEW;
      END $fn$""")
    op.execute("""CREATE TRIGGER auto_stock_serial_movement_guard
      BEFORE INSERT ON auto_stock_serial_movement FOR EACH ROW
      EXECUTE FUNCTION guard_auto_stock_serial_movement()""")
    op.execute("""CREATE FUNCTION check_auto_stock_serial_allocation()
      RETURNS trigger LANGUAGE plpgsql AS $fn$
      DECLARE active_count bigint; available bigint;
      BEGIN
        SELECT count(*) INTO active_count FROM auto_stock_serial_current
        WHERE lot_id=NEW.lot_id AND state='IN_STOCK';
        SELECT allocated INTO available FROM auto_stock_lot_balance
        WHERE lot_id=NEW.lot_id;
        IF active_count>COALESCE(available,0) THEN
           RAISE EXCEPTION 'lot movement strands registered serial identities';
        END IF;
        RETURN NULL;
      END $fn$""")
    op.execute("""CREATE CONSTRAINT TRIGGER auto_stock_serial_allocation_guard
      AFTER INSERT ON auto_stock_lot_allocation
      DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
      EXECUTE FUNCTION check_auto_stock_serial_allocation()""")
    # A plain quantitative WorkOrder RETURN must not consume outstanding
    # issues whose individual serial identities are still in ISSUED state.
    op.execute("""CREATE FUNCTION check_auto_stock_serial_return_outstanding()
      RETURNS trigger LANGUAGE plpgsql AS $fn$
      DECLARE physical_outstanding bigint; serialized_outstanding bigint;
      BEGIN
        SELECT COALESCE(SUM(q),0) INTO physical_outstanding FROM (
          SELECT quantity q FROM auto_stock_lot_work_order_issue
            WHERE work_order_id=NEW.work_order_id AND lot_id=NEW.lot_id
          UNION ALL
          SELECT -quantity q FROM auto_stock_lot_work_order_return
            WHERE work_order_id=NEW.work_order_id AND lot_id=NEW.lot_id
        ) amounts;
        SELECT count(*) INTO serialized_outstanding FROM auto_stock_serial_current
          WHERE lot_id=NEW.lot_id AND work_order_id=NEW.work_order_id
            AND state='ISSUED';
        IF serialized_outstanding>physical_outstanding THEN
          RAISE EXCEPTION 'quantitative return strands issued serial identity';
        END IF;
        RETURN NULL;
      END $fn$""")
    op.execute("""CREATE CONSTRAINT TRIGGER auto_stock_serial_return_outstanding
      AFTER INSERT ON auto_stock_lot_work_order_return
      DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
      EXECUTE FUNCTION check_auto_stock_serial_return_outstanding()""")
    op.execute("""CREATE FUNCTION record_auto_stock_serial_movement(
      p_serial uuid,p_request uuid,p_action text,p_order uuid,p_destination uuid
    ) RETURNS uuid LANGUAGE plpgsql AS $fn$
      DECLARE current_row record; destination_row record; prior record;
              stock_id uuid; result_id uuid;
      BEGIN
        IF p_serial IS NULL OR p_request IS NULL OR p_action IS NULL
         OR p_action NOT IN ('ISSUE','RETURN','TRANSFER')
         OR (p_action IN ('ISSUE','RETURN') AND (p_order IS NULL OR p_destination IS NOT NULL))
         OR (p_action='TRANSFER' AND (p_destination IS NULL OR p_order IS NOT NULL))
        THEN RAISE EXCEPTION 'invalid serial movement'; END IF;
        SELECT * INTO current_row FROM auto_stock_serial_current WHERE serial_id=p_serial;
        IF NOT FOUND THEN RAISE EXCEPTION 'unknown serial identity'; END IF;
        IF p_action='TRANSFER' THEN
          SELECT sku_id INTO destination_row FROM auto_stock_lot WHERE id=p_destination;
          IF NOT FOUND THEN RAISE EXCEPTION 'unknown serial destination'; END IF;
          PERFORM 1 FROM auto_stock_sku
             WHERE id IN (current_row.sku_id,destination_row.sku_id)
             ORDER BY id FOR UPDATE;
        ELSE
          PERFORM 1 FROM auto_stock_sku WHERE id=current_row.sku_id FOR UPDATE;
        END IF;
        SELECT * INTO prior FROM auto_stock_serial_movement WHERE request_id=p_request;
        IF FOUND THEN
          IF prior.serial_id=p_serial AND prior.action=p_action
            AND prior.work_order_id IS NOT DISTINCT FROM p_order
            AND prior.destination_lot_id IS NOT DISTINCT FROM p_destination
          THEN RETURN prior.id; END IF;
          RAISE EXCEPTION 'serial movement replay mismatch'; END IF;
        SELECT * INTO current_row FROM auto_stock_serial_current WHERE serial_id=p_serial;
        IF p_action='ISSUE' AND current_row.state='IN_STOCK' THEN
          stock_id:=record_auto_stock_lot_work_order_issue(
            p_order,current_row.lot_id,gen_random_uuid(),1);
        ELSIF p_action='RETURN' AND current_row.state='ISSUED'
               AND current_row.work_order_id=p_order THEN
          stock_id:=record_auto_stock_lot_work_order_return(
            p_order,current_row.lot_id,gen_random_uuid(),1);
        ELSIF p_action='TRANSFER' AND current_row.state='IN_STOCK' THEN
          stock_id:=record_auto_stock_interbranch_transfer(
            current_row.lot_id,p_destination,gen_random_uuid(),1);
        ELSE RAISE EXCEPTION 'invalid serial lifecycle transition'; END IF;
        result_id:=gen_random_uuid();
        INSERT INTO auto_stock_serial_movement(
           id,request_id,serial_id,action,source_lot_id,destination_lot_id,
           work_order_id,issue_id,return_id,interbranch_id,performed_by)
        VALUES(result_id,p_request,p_serial,p_action,current_row.lot_id,
           p_destination,p_order,
           CASE WHEN p_action='ISSUE' THEN stock_id END,
           CASE WHEN p_action='RETURN' THEN stock_id END,
           CASE WHEN p_action='TRANSFER' THEN stock_id END,
           NULLIF(current_setting('roboport.serial_actor',true),'')::uuid);
        RETURN result_id;
      END $fn$""")
    op.execute("REVOKE ALL ON auto_stock_serial_movement FROM PUBLIC")
    op.execute("REVOKE ALL ON auto_stock_serial_current FROM PUBLIC")
    op.execute("""REVOKE ALL ON FUNCTION record_auto_stock_serial_movement(
      uuid,uuid,text,uuid,uuid) FROM PUBLIC""")


def downgrade():
    op.execute("DROP FUNCTION record_auto_stock_serial_movement(uuid,uuid,text,uuid,uuid)")
    op.execute("""DROP TRIGGER auto_stock_serial_return_outstanding
      ON auto_stock_lot_work_order_return""")
    op.execute("DROP FUNCTION check_auto_stock_serial_return_outstanding()")
    op.execute("""CREATE OR REPLACE FUNCTION guard_auto_stock_serial()
      RETURNS trigger LANGUAGE plpgsql AS $fn$
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
        SELECT allocated INTO qty FROM auto_stock_lot_balance WHERE lot_id=NEW.lot_id;
        IF (SELECT count(*) FROM auto_stock_serial WHERE lot_id=NEW.lot_id)>=qty
        THEN RAISE EXCEPTION 'serial registrations exceed placed lot stock'; END IF;
        NEW.registered_by:=actor;
        RETURN NEW;
      END $fn$""")
    op.execute("DROP TRIGGER auto_stock_serial_allocation_guard ON auto_stock_lot_allocation")
    op.execute("DROP FUNCTION check_auto_stock_serial_allocation()")
    op.execute("DROP VIEW auto_stock_serial_current")
    op.execute("DROP TABLE auto_stock_serial_movement")
    op.execute("DROP FUNCTION guard_auto_stock_serial_movement()")
