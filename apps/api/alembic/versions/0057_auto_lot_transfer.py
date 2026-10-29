"""Atomic same-SKU transfer of existing allocation between lots.

Revision ID: 0057_auto_lot_transfer
Revises: 0056_auto_lot_allocation
"""
from alembic import op

revision = "0057_auto_lot_transfer"
down_revision = "0056_auto_lot_allocation"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
      CREATE TABLE auto_stock_lot_transfer (
        id uuid PRIMARY KEY,
        request_id uuid NOT NULL UNIQUE,
        source_lot_id uuid NOT NULL REFERENCES auto_stock_lot(id),
        destination_lot_id uuid NOT NULL REFERENCES auto_stock_lot(id),
        sku_id uuid NOT NULL REFERENCES auto_stock_sku(id),
        quantity bigint NOT NULL CHECK (quantity BETWEEN 1 AND 1000000000),
        debit_event_id uuid NOT NULL UNIQUE REFERENCES auto_stock_lot_allocation(id),
        credit_event_id uuid NOT NULL UNIQUE REFERENCES auto_stock_lot_allocation(id),
        created_at timestamptz NOT NULL DEFAULT now(),
        CHECK (source_lot_id<>destination_lot_id),
        CHECK (debit_event_id<>credit_event_id)
      )
    """)
    op.execute("""
      CREATE TRIGGER auto_stock_lot_transfer_immutable
      BEFORE UPDATE OR DELETE ON auto_stock_lot_transfer
      FOR EACH ROW EXECUTE FUNCTION immutable_auto_stock_event()
    """)
    op.execute("""
      CREATE FUNCTION check_auto_stock_lot_transfer()
      RETURNS trigger LANGUAGE plpgsql AS $fn$
      BEGIN
        IF NOT EXISTS (
          SELECT 1 FROM auto_stock_lot_allocation d
          JOIN auto_stock_lot_allocation c ON c.id=NEW.credit_event_id
          WHERE d.id=NEW.debit_event_id AND d.lot_id=NEW.source_lot_id
            AND c.lot_id=NEW.destination_lot_id AND d.sku_id=NEW.sku_id
            AND c.sku_id=NEW.sku_id AND d.action='DEALLOCATE'
            AND c.action='ALLOCATE' AND d.quantity=NEW.quantity
            AND c.quantity=NEW.quantity
        ) THEN
          RAISE EXCEPTION 'invalid transfer pair';
        END IF;
        RETURN NULL;
      END $fn$
    """)
    op.execute("""
      CREATE CONSTRAINT TRIGGER auto_stock_lot_transfer_pair
      AFTER INSERT ON auto_stock_lot_transfer
      DEFERRABLE INITIALLY DEFERRED
      FOR EACH ROW EXECUTE FUNCTION check_auto_stock_lot_transfer()
    """)
    op.execute("""
      CREATE FUNCTION record_auto_stock_lot_transfer(
        p_source uuid,p_destination uuid,p_request uuid,p_quantity bigint
      ) RETURNS uuid LANGUAGE plpgsql AS $fn$
      DECLARE source_row record; dest_row record; prior record;
              transfer_id uuid; debit_id uuid; credit_id uuid;
      BEGIN
        IF p_source IS NULL OR p_destination IS NULL OR p_request IS NULL
           OR p_source=p_destination OR p_quantity IS NULL
           OR p_quantity<1 OR p_quantity>1000000000 THEN
          RAISE EXCEPTION 'invalid lot transfer';
        END IF;
        SELECT l.sku_id,l.organization_id,l.location_id INTO source_row
          FROM auto_stock_lot l
          JOIN auto_service_location loc ON loc.id=l.location_id
            AND loc.organization_id=l.organization_id
          JOIN auto_organization org ON org.id=l.organization_id
          WHERE l.id=p_source AND loc.disabled_at IS NULL AND org.disabled_at IS NULL;
        IF NOT FOUND THEN RAISE EXCEPTION 'source unavailable'; END IF;
        SELECT l.sku_id,l.organization_id,l.location_id INTO dest_row
          FROM auto_stock_lot l
          JOIN auto_service_location loc ON loc.id=l.location_id
            AND loc.organization_id=l.organization_id
          JOIN auto_organization org ON org.id=l.organization_id
          WHERE l.id=p_destination AND loc.disabled_at IS NULL AND org.disabled_at IS NULL;
        IF NOT FOUND OR (source_row.sku_id,source_row.organization_id,source_row.location_id)
                        IS DISTINCT FROM
                        (dest_row.sku_id,dest_row.organization_id,dest_row.location_id) THEN
          RAISE EXCEPTION 'transfer outside same SKU and branch';
        END IF;
        -- Lock the single SKU for both legs, in the same order as standalone
        -- allocation and physical issue, and keep it until commit.
        PERFORM 1 FROM auto_stock_sku WHERE id=source_row.sku_id FOR UPDATE;
        SELECT * INTO prior FROM auto_stock_lot_transfer WHERE request_id=p_request;
        IF FOUND THEN
          IF prior.source_lot_id=p_source AND prior.destination_lot_id=p_destination
             AND prior.quantity=p_quantity AND prior.sku_id=source_row.sku_id THEN
            RETURN prior.id;
          END IF;
          RAISE EXCEPTION 'transfer request replay mismatch';
        END IF;
        debit_id:=gen_random_uuid();
        credit_id:=gen_random_uuid();
        transfer_id:=gen_random_uuid();
        INSERT INTO auto_stock_lot_allocation
          (id,request_id,lot_id,sku_id,action,quantity)
          VALUES(debit_id,gen_random_uuid(),p_source,source_row.sku_id,'DEALLOCATE',p_quantity);
        INSERT INTO auto_stock_lot_allocation
          (id,request_id,lot_id,sku_id,action,quantity)
          VALUES(credit_id,gen_random_uuid(),p_destination,source_row.sku_id,'ALLOCATE',p_quantity);
        INSERT INTO auto_stock_lot_transfer
          (id,request_id,source_lot_id,destination_lot_id,sku_id,quantity,debit_event_id,credit_event_id)
          VALUES(transfer_id,p_request,p_source,p_destination,source_row.sku_id,
                 p_quantity,debit_id,credit_id);
        RETURN transfer_id;
      END $fn$
    """)
    op.execute("REVOKE ALL ON FUNCTION record_auto_stock_lot_transfer(uuid,uuid,uuid,bigint) FROM PUBLIC")


def downgrade():
    # Disposable PG17 only: destroys transfer history and references.
    op.execute("DROP FUNCTION record_auto_stock_lot_transfer(uuid,uuid,uuid,bigint)")
    op.execute("DROP TABLE auto_stock_lot_transfer")
    op.execute("DROP FUNCTION check_auto_stock_lot_transfer()")
