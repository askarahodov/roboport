"""Идентифицированное выбытие серии только по независимому акту (#168)."""
from alembic import op
from sqlalchemy import text

revision = "0147_auto_serial_disposition"
down_revision = "0145_auto_return_terminal"
branch_labels = None
depends_on = None


def upgrade():
    for table in ("auto_stock_writeoff_document","auto_stock_supplier_return_document"):
        op.execute(f"""
          ALTER TABLE {table} ADD COLUMN serial_ids uuid[] NOT NULL DEFAULT '{{}}'
            CHECK (cardinality(serial_ids)<=50
              AND (cardinality(serial_ids)=0 OR cardinality(serial_ids)=quantity))
        """)
    op.execute("""
      CREATE TABLE auto_stock_serial_disposition (
        serial_id uuid PRIMARY KEY REFERENCES auto_stock_serial(id),
        disposition_id uuid NOT NULL REFERENCES auto_stock_lot_disposition(id),
        action text NOT NULL CHECK(action IN ('WRITE_OFF','SUPPLIER_RETURN')),
        writeoff_decision_id uuid REFERENCES auto_stock_writeoff_decision(id),
        supplier_acceptance_id uuid REFERENCES auto_stock_supplier_return_acceptance(id),
        actor_id uuid NOT NULL REFERENCES app_user(id),
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        CHECK ((action='WRITE_OFF' AND writeoff_decision_id IS NOT NULL AND supplier_acceptance_id IS NULL)
           OR (action='SUPPLIER_RETURN' AND supplier_acceptance_id IS NOT NULL AND writeoff_decision_id IS NULL))
      )
    """)
    op.execute("CREATE INDEX ix_auto_serial_disposition_physical ON auto_stock_serial_disposition(disposition_id)")
    op.execute("""CREATE OR REPLACE VIEW auto_stock_serial_current AS
      SELECT s.id AS serial_id,s.organization_id,s.serial_number,s.registered_by,
             l.id AS lot_id,l.sku_id,l.location_id,
             CASE WHEN terminal.action='WRITE_OFF' THEN 'WRITTEN_OFF'
                  WHEN terminal.action='SUPPLIER_RETURN' THEN 'SUPPLIER_RETURNED'
                  WHEN m.action='ISSUE' THEN 'ISSUED' ELSE 'IN_STOCK' END AS state,
             CASE WHEN m.action='ISSUE' THEN m.work_order_id ELSE NULL END AS work_order_id,
             m.id AS last_movement_id
      FROM auto_stock_serial s
      LEFT JOIN LATERAL (
        SELECT e.* FROM auto_stock_serial_movement e WHERE e.serial_id=s.id
        ORDER BY e.movement_sequence DESC LIMIT 1
      ) m ON true
      LEFT JOIN auto_stock_serial_disposition terminal ON terminal.serial_id=s.id
      JOIN auto_stock_lot l
        ON l.id=COALESCE(m.destination_lot_id,m.source_lot_id,s.lot_id)""")
    op.execute("""
      CREATE FUNCTION check_auto_serial_disposition_selection(p_lot uuid,p_receipt uuid,p_ids uuid[])
      RETURNS void LANGUAGE plpgsql AS $fn$
      DECLARE sku uuid;
      BEGIN
        IF cardinality(p_ids)=0 THEN RETURN; END IF;
        IF array_ndims(p_ids)<>1 OR array_position(p_ids,NULL::uuid) IS NOT NULL
           OR (SELECT count(DISTINCT id) FROM unnest(p_ids) AS x(id))<>cardinality(p_ids)
        THEN RAISE EXCEPTION 'Список серий неоднозначен'; END IF;
        SELECT sku_id INTO sku FROM auto_stock_lot WHERE id=p_lot;
        IF NOT FOUND THEN RAISE EXCEPTION 'Партия недоступна'; END IF;
        PERFORM 1 FROM auto_stock_sku WHERE id=sku FOR UPDATE;
        IF (SELECT count(*) FROM auto_stock_serial_current c
            WHERE c.serial_id=ANY(p_ids) AND c.lot_id=p_lot AND c.state='IN_STOCK')
           <>cardinality(p_ids) THEN RAISE EXCEPTION 'Серия недоступна в партии'; END IF;
        IF p_receipt IS NOT NULL AND (SELECT count(*) FROM auto_stock_serial_receipt_assertion a
              JOIN auto_stock_serial_receipt_approval p ON p.assertion_id=a.id
              WHERE a.serial_id=ANY(p_ids) AND a.receipt_id=p_receipt)<>cardinality(p_ids)
        THEN RAISE EXCEPTION 'Нет утверждённого происхождения серии из указанного прихода'; END IF;
      END $fn$
    """)
    op.execute("""
      CREATE FUNCTION guard_auto_serial_disposition_draft() RETURNS trigger LANGUAGE plpgsql AS $fn$
      BEGIN
        IF TG_TABLE_NAME='auto_stock_supplier_return_document' THEN
          PERFORM check_auto_serial_disposition_selection(NEW.lot_id,NEW.source_receipt_id,NEW.serial_ids);
        ELSE
          PERFORM check_auto_serial_disposition_selection(NEW.lot_id,NULL,NEW.serial_ids);
        END IF;
        RETURN NEW;
      END $fn$
    """)
    for table in ("auto_stock_writeoff_document","auto_stock_supplier_return_document"):
        op.execute(f"""CREATE TRIGGER {table}_serial_selection BEFORE INSERT ON {table}
          FOR EACH ROW EXECUTE FUNCTION guard_auto_serial_disposition_draft()""")
    op.execute("""
      CREATE FUNCTION guard_auto_serial_disposition() RETURNS trigger LANGUAGE plpgsql AS $fn$
      DECLARE doc record; p record;
      BEGIN
        SELECT * INTO p FROM auto_stock_lot_disposition WHERE id=NEW.disposition_id;
        IF NOT FOUND OR p.action<>NEW.action THEN RAISE EXCEPTION 'Нет связанного выбытия'; END IF;
        IF NEW.action='WRITE_OFF' THEN
          SELECT d.lot_id,d.quantity,d.serial_ids,x.actor_id AS actor,x.created_at AS stamp,
                 NULL::uuid AS receipt INTO doc
            FROM auto_stock_writeoff_decision x JOIN auto_stock_writeoff_document d ON d.id=x.document_id
            WHERE x.id=NEW.writeoff_decision_id AND x.action='APPROVE' AND x.disposition_id=p.id;
        ELSE
          SELECT d.lot_id,d.quantity,d.serial_ids,a.accepted_by AS actor,a.created_at AS stamp,
                 d.source_receipt_id AS receipt INTO doc
            FROM auto_stock_supplier_return_acceptance a
            JOIN auto_stock_supplier_return_document d ON d.id=a.document_id
            WHERE a.id=NEW.supplier_acceptance_id AND a.disposition_id=p.id;
        END IF;
        IF NOT FOUND OR doc.lot_id<>p.lot_id OR doc.quantity<>p.quantity
           OR NOT (NEW.serial_id=ANY(doc.serial_ids)) OR doc.actor<>NEW.actor_id
           OR EXISTS (SELECT 1 FROM auto_stock_serial_disposition e WHERE e.disposition_id=p.id
                     AND (e.writeoff_decision_id,e.supplier_acceptance_id)
                       IS DISTINCT FROM (NEW.writeoff_decision_id,NEW.supplier_acceptance_id))
        THEN RAISE EXCEPTION 'Выбытие не соответствует утверждённому акту'; END IF;
        PERFORM check_auto_serial_disposition_selection(doc.lot_id,doc.receipt,ARRAY[NEW.serial_id]);
        NEW.created_at:=doc.stamp;
        RETURN NEW;
      END $fn$
    """)
    op.execute("""CREATE TRIGGER auto_serial_disposition_guard BEFORE INSERT ON auto_stock_serial_disposition
      FOR EACH ROW EXECUTE FUNCTION guard_auto_serial_disposition()""")
    op.execute("""CREATE TRIGGER auto_serial_disposition_immutable BEFORE UPDATE OR DELETE ON auto_stock_serial_disposition
      FOR EACH ROW EXECUTE FUNCTION immutable_auto_stock_event()""")
    op.execute("""CREATE TRIGGER auto_serial_disposition_no_truncate BEFORE TRUNCATE ON auto_stock_serial_disposition
      FOR EACH STATEMENT EXECUTE FUNCTION immutable_auto_stock_event()""")
    op.execute("""
      CREATE FUNCTION record_auto_serial_disposition() RETURNS trigger LANGUAGE plpgsql AS $fn$
      DECLARE d record;
      BEGIN
        IF TG_TABLE_NAME='auto_stock_writeoff_decision' THEN
          IF NEW.action<>'APPROVE' THEN RETURN NULL; END IF;
          SELECT lot_id,serial_ids INTO d FROM auto_stock_writeoff_document WHERE id=NEW.document_id;
          PERFORM check_auto_serial_disposition_selection(d.lot_id,NULL,d.serial_ids);
          INSERT INTO auto_stock_serial_disposition(serial_id,disposition_id,action,writeoff_decision_id,actor_id)
          SELECT id,NEW.disposition_id,'WRITE_OFF',NEW.id,NEW.actor_id FROM unnest(d.serial_ids) x(id);
        ELSE
          SELECT lot_id,serial_ids,source_receipt_id INTO d FROM auto_stock_supplier_return_document
            WHERE id=NEW.document_id;
          PERFORM check_auto_serial_disposition_selection(d.lot_id,d.source_receipt_id,d.serial_ids);
          INSERT INTO auto_stock_serial_disposition(serial_id,disposition_id,action,supplier_acceptance_id,actor_id)
          SELECT id,NEW.disposition_id,'SUPPLIER_RETURN',NEW.id,NEW.accepted_by FROM unnest(d.serial_ids) x(id);
        END IF;
        RETURN NULL;
      END $fn$
    """)
    op.execute("""CREATE TRIGGER auto_serial_writeoff_terminal AFTER INSERT ON auto_stock_writeoff_decision
      FOR EACH ROW EXECUTE FUNCTION record_auto_serial_disposition()""")
    op.execute("""CREATE TRIGGER auto_serial_supplier_terminal AFTER INSERT ON auto_stock_supplier_return_acceptance
      FOR EACH ROW EXECUTE FUNCTION record_auto_serial_disposition()""")
    op.execute("REVOKE ALL ON auto_stock_serial_disposition FROM PUBLIC")
    for function in ("check_auto_serial_disposition_selection(uuid,uuid,uuid[])",
                     "guard_auto_serial_disposition_draft()","guard_auto_serial_disposition()",
                     "record_auto_serial_disposition()"):
        op.execute(f"REVOKE ALL ON FUNCTION {function} FROM PUBLIC")


def downgrade():
    if op.get_bind().execute(text("""
        SELECT 1 FROM auto_stock_serial_disposition
        UNION ALL SELECT 1 FROM auto_stock_writeoff_document WHERE cardinality(serial_ids)>0
        UNION ALL SELECT 1 FROM auto_stock_supplier_return_document WHERE cardinality(serial_ids)>0
        LIMIT 1
    """)).first():
        raise RuntimeError("История серийного выбытия существует: требуется согласованный rollback")
    op.execute("DROP TRIGGER auto_serial_writeoff_terminal ON auto_stock_writeoff_decision")
    op.execute("DROP TRIGGER auto_serial_supplier_terminal ON auto_stock_supplier_return_acceptance")
    op.execute("""CREATE OR REPLACE VIEW auto_stock_serial_current AS
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
    op.execute("DROP TABLE auto_stock_serial_disposition")
    for table in ("auto_stock_writeoff_document","auto_stock_supplier_return_document"):
        op.execute(f"DROP TRIGGER {table}_serial_selection ON {table}")
        op.execute(f"ALTER TABLE {table} DROP COLUMN serial_ids")
    for function in ("record_auto_serial_disposition()","guard_auto_serial_disposition()",
                     "guard_auto_serial_disposition_draft()","check_auto_serial_disposition_selection(uuid,uuid,uuid[])"):
        op.execute(f"DROP FUNCTION {function}")
