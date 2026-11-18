"""Independent serial-to-receipt attestation for mixed priced lots (dev only).

Revision ID: 0131_auto_serial_receipt_link
Revises: 0130_auto_internal_labor_cost
"""
from alembic import op

revision="0131_auto_serial_receipt_link"
down_revision="0130_auto_internal_labor_cost"
branch_labels=None
depends_on=None


def upgrade():
    op.execute("""
      CREATE TABLE auto_stock_serial_receipt_assertion(
        id uuid PRIMARY KEY,
        serial_id uuid NOT NULL UNIQUE REFERENCES auto_stock_serial(id),
        receipt_id uuid NOT NULL REFERENCES auto_stock_receipt_document(id),
        proposed_by uuid NOT NULL REFERENCES app_user(id),
        request_key uuid NOT NULL UNIQUE,
        cost_registration_seq bigint NOT NULL UNIQUE CHECK(cost_registration_seq>0),
        created_at timestamptz NOT NULL DEFAULT clock_timestamp()
      )
    """)
    op.execute("""
      CREATE TABLE auto_stock_serial_receipt_approval(
        id uuid PRIMARY KEY,
        assertion_id uuid NOT NULL UNIQUE REFERENCES auto_stock_serial_receipt_assertion(id),
        approved_by uuid NOT NULL REFERENCES app_user(id),
        request_key uuid NOT NULL UNIQUE,
        cost_registration_seq bigint NOT NULL UNIQUE CHECK(cost_registration_seq>0),
        created_at timestamptz NOT NULL DEFAULT clock_timestamp()
      )
    """)
    op.execute("""
      CREATE FUNCTION guard_auto_serial_receipt_provenance()
      RETURNS trigger LANGUAGE plpgsql AS $fn$
      DECLARE actor uuid; serial_row record; receipt_row record;
              maker uuid; assertion_row record; source_lot uuid;
              scoped_serial uuid; scoped_receipt uuid; scoped_sku uuid;
              required_role text;
      BEGIN
        actor:=NULLIF(current_setting('roboport.serial_receipt_actor',true),'')::uuid;
        IF actor IS NULL THEN RAISE EXCEPTION 'serial receipt actor required'; END IF;
        IF TG_TABLE_NAME='auto_stock_serial_receipt_assertion' THEN
          SELECT s.*,l.organization_id AS scoped_org,l.location_id AS scoped_loc,
                 l.sku_id AS scoped_sku INTO serial_row
          FROM auto_stock_serial s JOIN auto_stock_lot l ON l.id=s.lot_id
          WHERE s.id=NEW.serial_id;
          IF NOT FOUND THEN RAISE EXCEPTION 'serial not found'; END IF;
          SELECT d.*,a.id AS acceptance_id INTO receipt_row
          FROM auto_stock_receipt_document d
          JOIN auto_stock_receipt_acceptance a ON a.document_id=d.id
          WHERE d.id=NEW.receipt_id AND d.lot_id=serial_row.lot_id
            AND d.sku_id=serial_row.scoped_sku
            AND d.organization_id=serial_row.scoped_org
            AND d.location_id=serial_row.scoped_loc;
          IF NOT FOUND THEN RAISE EXCEPTION 'receipt not accepted in serial lot'; END IF;
          source_lot:=serial_row.lot_id;
          scoped_serial:=NEW.serial_id;
          scoped_receipt:=NEW.receipt_id;
          scoped_sku:=serial_row.scoped_sku;
          maker:=NULL;
          required_role:='WAREHOUSE';
        ELSE
          SELECT a.*,d.lot_id,d.organization_id AS scoped_org,
                 d.location_id AS scoped_loc,d.sku_id AS scoped_sku
            INTO assertion_row
          FROM auto_stock_serial_receipt_assertion a
          JOIN auto_stock_receipt_document d ON d.id=a.receipt_id
          WHERE a.id=NEW.assertion_id;
          IF NOT FOUND THEN RAISE EXCEPTION 'serial assertion not found'; END IF;
          source_lot:=assertion_row.lot_id;
          scoped_serial:=assertion_row.serial_id;
          scoped_receipt:=assertion_row.receipt_id;
          scoped_sku:=assertion_row.scoped_sku;
          maker:=assertion_row.proposed_by;
          required_role:='MANAGER';
          IF maker=actor THEN RAISE EXCEPTION 'independent serial source approval required'; END IF;
        END IF;
        PERFORM 1 FROM auto_stock_sku WHERE id=scoped_sku
          FOR UPDATE;
        -- No backdating provenance for a serial which has already moved.
        IF EXISTS(SELECT 1 FROM auto_stock_serial_movement m
          WHERE m.serial_id=scoped_serial)
        THEN RAISE EXCEPTION 'serial receipt lineage must precede movement'; END IF;
        IF NOT EXISTS(SELECT 1 FROM auto_stock_serial_current current
          WHERE current.serial_id=scoped_serial
            AND current.lot_id=source_lot AND current.state='IN_STOCK')
        THEN RAISE EXCEPTION 'serial not in accepted receipt lot'; END IF;
        IF NOT EXISTS(
          SELECT 1 FROM auto_stock_receipt_document d
          JOIN auto_organization o ON o.id=d.organization_id
          JOIN auto_service_location l ON l.id=d.location_id
          JOIN auto_organization_role_grant g
            ON g.organization_id=d.organization_id
            AND g.user_id=actor AND g.role_code=required_role
            AND g.revoked_at IS NULL
            AND (g.location_id=d.location_id OR g.location_id IS NULL)
          JOIN app_user u ON u.id=g.user_id AND u.status='ACTIVE'
          WHERE d.id=scoped_receipt
            AND o.disabled_at IS NULL AND l.disabled_at IS NULL
          FOR SHARE OF o,l,g,u
        ) THEN RAISE EXCEPTION 'serial provenance authority required'; END IF;
        IF TG_TABLE_NAME='auto_stock_serial_receipt_assertion' THEN
          IF (SELECT count(*) FROM auto_stock_serial_receipt_assertion
                WHERE receipt_id=NEW.receipt_id)>=receipt_row.quantity
          THEN RAISE EXCEPTION 'serial links exceed receipt quantity'; END IF;
          NEW.proposed_by:=actor;
        ELSE
          NEW.approved_by:=actor;
        END IF;
        NEW.cost_registration_seq:=nextval('auto_cost_registration_seq');
        NEW.created_at:=clock_timestamp();
        RETURN NEW;
      END $fn$
    """)
    for table in ("auto_stock_serial_receipt_assertion","auto_stock_serial_receipt_approval"):
        op.execute(f"""CREATE TRIGGER {table}_guard BEFORE INSERT ON {table}
          FOR EACH ROW EXECUTE FUNCTION guard_auto_serial_receipt_provenance()""")
        op.execute(f"""CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table}
          FOR EACH ROW EXECUTE FUNCTION immutable_auto_stock_event()""")
        op.execute(f"REVOKE ALL ON {table} FROM PUBLIC")


def downgrade():
    # Disposable PG17 only; destroying recorded source attestations is not
    # an acceptable production rollback.
    op.execute("DROP TABLE auto_stock_serial_receipt_approval")
    op.execute("DROP TABLE auto_stock_serial_receipt_assertion")
    op.execute("DROP FUNCTION guard_auto_serial_receipt_provenance()")
