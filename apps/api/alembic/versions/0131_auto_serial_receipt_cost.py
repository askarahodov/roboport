"""Double-verified serial-to-receipt provenance before first physical movement.

Revision ID: 0131_auto_serial_receipt_cost
Revises: 0130_auto_internal_labor_cost
Synthetic-only; no backfilling historical serial movements.
"""
from alembic import op

revision = "0131_auto_serial_receipt_cost"
down_revision = "0130_auto_internal_labor_cost"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
      CREATE TABLE auto_stock_serial_receipt_assertion (
        id uuid PRIMARY KEY,
        serial_id uuid NOT NULL UNIQUE REFERENCES auto_stock_serial(id),
        receipt_id uuid NOT NULL REFERENCES auto_stock_receipt_document(id),
        organization_id uuid NOT NULL REFERENCES auto_organization(id),
        location_id uuid NOT NULL REFERENCES auto_service_location(id),
        asserted_by uuid NOT NULL REFERENCES app_user(id),
        request_key uuid NOT NULL UNIQUE,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp()
      )
    """)
    op.execute("""
      CREATE TABLE auto_stock_serial_receipt_approval (
        id uuid PRIMARY KEY,
        assertion_id uuid NOT NULL UNIQUE REFERENCES auto_stock_serial_receipt_assertion(id),
        approved_by uuid NOT NULL REFERENCES app_user(id),
        request_key uuid NOT NULL UNIQUE,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp()
      )
    """)
    op.execute("""
      CREATE FUNCTION guard_auto_stock_serial_receipt()
      RETURNS trigger LANGUAGE plpgsql AS $f$
      DECLARE v_sku uuid; v_lot uuid; v_loc uuid; v_org uuid;
              v_actor uuid; v_maker uuid; v_qty bigint; v_serial uuid;
      BEGIN
        IF TG_TABLE_NAME='auto_stock_serial_receipt_assertion' THEN
          v_serial:=NEW.serial_id;
          v_actor:=NEW.asserted_by;
        ELSE
          SELECT s.serial_id,s.asserted_by INTO v_serial,v_maker
            FROM auto_stock_serial_receipt_assertion s WHERE s.id=NEW.assertion_id
            FOR SHARE;
          IF NOT FOUND OR NEW.approved_by=v_maker THEN
            RAISE EXCEPTION 'independent serial receipt approval required';
          END IF;
          v_actor:=NEW.approved_by;
        END IF;
        IF NULLIF(current_setting('roboport.serial_receipt_actor',true),'')::uuid
            IS DISTINCT FROM v_actor THEN
          RAISE EXCEPTION 'serial receipt authenticated actor mismatch';
        END IF;
        SELECT s.organization_id,s.location_id,s.lot_id,s.sku_id
          INTO v_org,v_loc,v_lot,v_sku FROM auto_stock_serial s
          WHERE s.id=v_serial FOR SHARE;
        IF NOT FOUND THEN RAISE EXCEPTION 'serial identity unavailable'; END IF;
        PERFORM 1 FROM auto_stock_sku WHERE id=v_sku FOR UPDATE;
        -- Lock AFTER reading source serial, then check again under SKU mutex.
        IF EXISTS(SELECT 1 FROM auto_stock_serial_movement WHERE serial_id=v_serial)
        THEN RAISE EXCEPTION 'serial has already moved'; END IF;
        PERFORM 1 FROM auto_organization o WHERE o.id=v_org AND o.disabled_at IS NULL FOR SHARE;
        IF NOT FOUND THEN RAISE EXCEPTION 'serial organization disabled'; END IF;
        PERFORM 1 FROM auto_service_location l WHERE l.id=v_loc
          AND l.organization_id=v_org AND l.disabled_at IS NULL FOR SHARE;
        IF NOT FOUND THEN RAISE EXCEPTION 'serial location disabled'; END IF;
        PERFORM 1 FROM auto_organization_role_grant g
          JOIN app_user u ON u.id=g.user_id AND u.status='ACTIVE'
          WHERE g.user_id=v_actor AND g.organization_id=v_org
            AND g.role_code=CASE
              WHEN TG_TABLE_NAME='auto_stock_serial_receipt_assertion'
              THEN 'WAREHOUSE' ELSE 'MANAGER' END
            AND g.revoked_at IS NULL
            AND (g.location_id=v_loc OR g.location_id IS NULL)
          FOR SHARE OF g,u;
        IF NOT FOUND THEN RAISE EXCEPTION 'serial receipt role unavailable'; END IF;
        IF TG_TABLE_NAME='auto_stock_serial_receipt_assertion' THEN
          IF (NEW.organization_id,NEW.location_id)
             IS DISTINCT FROM (v_org,v_loc)
             OR NOT EXISTS(
                SELECT 1 FROM auto_stock_receipt_document d
                JOIN auto_stock_receipt_acceptance ac ON ac.document_id=d.id
                WHERE d.id=NEW.receipt_id AND d.lot_id=v_lot AND d.sku_id=v_sku
                  AND d.organization_id=v_org AND d.location_id=v_loc
             ) THEN RAISE EXCEPTION 'receipt not accepted for serial lot'; END IF;
          SELECT quantity INTO v_qty FROM auto_stock_receipt_document
            WHERE id=NEW.receipt_id FOR SHARE;
          IF (SELECT COUNT(*) FROM auto_stock_serial_receipt_assertion
              WHERE receipt_id=NEW.receipt_id)>=v_qty
          THEN RAISE EXCEPTION 'receipt serial attribution exceeds quantity'; END IF;
        END IF;
        NEW.created_at:=clock_timestamp();
        RETURN NEW;
      END $f$
    """)
    for table in ("auto_stock_serial_receipt_assertion",
                  "auto_stock_serial_receipt_approval"):
        op.execute(f"""CREATE TRIGGER {table}_guard BEFORE INSERT ON {table}
          FOR EACH ROW EXECUTE FUNCTION guard_auto_stock_serial_receipt()""")
        op.execute(f"""CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table}
          FOR EACH ROW EXECUTE FUNCTION immutable_auto_stock_event()""")
        op.execute(f"REVOKE ALL ON {table} FROM PUBLIC")
    # Chronology of every NEW serial issue/return/transfer is evaluated against
    # accepted receipts and their cost_registration_seq, not now().
    op.execute("""
      ALTER TABLE auto_stock_serial_movement ADD COLUMN cost_registration_seq bigint
        CHECK(cost_registration_seq>0)
    """)
    op.execute("""
      CREATE FUNCTION stamp_auto_stock_serial_cost_seq()
      RETURNS trigger LANGUAGE plpgsql AS $f$
      BEGIN
        -- The preceding movement guard and the source serial service already
        -- hold the SKU mutex. Re-enter the same lock for direct SQL.
        PERFORM 1 FROM auto_stock_sku
          WHERE id=(SELECT sku_id FROM auto_stock_lot WHERE id=NEW.source_lot_id)
          FOR UPDATE;
        IF NOT FOUND THEN RAISE EXCEPTION 'serial movement SKU unavailable'; END IF;
        NEW.cost_registration_seq:=nextval('auto_cost_registration_seq');
        RETURN NEW;
      END $f$
    """)
    op.execute("""
      CREATE TRIGGER zz_auto_stock_serial_cost_seq BEFORE INSERT
      ON auto_stock_serial_movement FOR EACH ROW
      EXECUTE FUNCTION stamp_auto_stock_serial_cost_seq()
    """)


def downgrade():
    # Disposable synthetic PostgreSQL only; never erase real accounting evidence.
    op.execute("DROP TRIGGER zz_auto_stock_serial_cost_seq ON auto_stock_serial_movement")
    op.execute("DROP FUNCTION stamp_auto_stock_serial_cost_seq()")
    op.execute("ALTER TABLE auto_stock_serial_movement DROP COLUMN cost_registration_seq")
    op.execute("DROP TABLE auto_stock_serial_receipt_approval")
    op.execute("DROP TABLE auto_stock_serial_receipt_assertion")
    op.execute("DROP FUNCTION guard_auto_stock_serial_receipt()")
