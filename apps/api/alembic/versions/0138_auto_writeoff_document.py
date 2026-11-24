"""Документ выбытия: независимое решение и связанный физический журнал."""
from alembic import op
from sqlalchemy import text

revision="0138_auto_writeoff_document"
down_revision="0137_auto_transfer_receipt"
branch_labels=None
depends_on=None


def upgrade():
    op.execute("""
      CREATE TABLE auto_stock_writeoff_document (
        id uuid PRIMARY KEY,
        request_id uuid NOT NULL UNIQUE,
        organization_id uuid NOT NULL REFERENCES auto_organization(id),
        location_id uuid NOT NULL REFERENCES auto_service_location(id),
        lot_id uuid NOT NULL REFERENCES auto_stock_lot(id),
        quantity bigint NOT NULL CHECK(quantity BETWEEN 1 AND 1000000000),
        reason_code text NOT NULL CHECK(reason_code IN ('DAMAGED','EXPIRED','UNUSABLE','OTHER')),
        note text NOT NULL CHECK(length(btrim(note)) BETWEEN 3 AND 240 AND note=btrim(note)),
        created_by uuid NOT NULL REFERENCES app_user(id),
        created_at timestamptz NOT NULL DEFAULT clock_timestamp()
      )
    """)
    op.execute("""
      CREATE TABLE auto_stock_writeoff_decision (
        id uuid PRIMARY KEY,
        request_id uuid NOT NULL UNIQUE,
        document_id uuid NOT NULL UNIQUE REFERENCES auto_stock_writeoff_document(id),
        action text NOT NULL CHECK(action IN ('APPROVE','REJECT','CANCEL')),
        note text NOT NULL CHECK(length(btrim(note)) BETWEEN 3 AND 240 AND note=btrim(note)),
        actor_id uuid NOT NULL REFERENCES app_user(id),
        disposition_id uuid UNIQUE REFERENCES auto_stock_lot_disposition(id),
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        CHECK((action='APPROVE')=(disposition_id IS NOT NULL))
      )
    """)
    op.execute("CREATE INDEX ix_auto_writeoff_scope ON auto_stock_writeoff_document(organization_id,location_id,created_at,id)")
    for table in ("auto_stock_writeoff_document","auto_stock_writeoff_decision"):
        op.execute(f"""CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table}
                       FOR EACH ROW EXECUTE FUNCTION immutable_auto_stock_event()""")
    op.execute("""
      CREATE FUNCTION guard_auto_writeoff_document() RETURNS trigger LANGUAGE plpgsql AS $f$
      BEGIN
        IF NOT EXISTS(
          SELECT 1 FROM auto_stock_lot l JOIN auto_service_location b ON b.id=l.location_id
          JOIN auto_organization o ON o.id=l.organization_id
          WHERE l.id=NEW.lot_id AND l.organization_id=NEW.organization_id
            AND l.location_id=NEW.location_id AND b.organization_id=o.id
            AND b.disabled_at IS NULL AND o.disabled_at IS NULL
          FOR SHARE OF l,b,o
        ) THEN RAISE EXCEPTION 'Партия недоступна'; END IF;
        RETURN NEW;
      END $f$
    """)
    op.execute("""
      CREATE TRIGGER auto_writeoff_document_guard BEFORE INSERT ON auto_stock_writeoff_document
      FOR EACH ROW EXECUTE FUNCTION guard_auto_writeoff_document()
    """)
    op.execute("""
      CREATE FUNCTION guard_auto_writeoff_decision() RETURNS trigger LANGUAGE plpgsql AS $f$
      DECLARE d record;
      BEGIN
        SELECT * INTO d FROM auto_stock_writeoff_document WHERE id=NEW.document_id FOR UPDATE;
        IF NOT FOUND OR ((NEW.action='CANCEL')<>(NEW.actor_id=d.created_by))
          THEN RAISE EXCEPTION 'Требуется независимое решение либо отмена автором'; END IF;
        IF NEW.action='APPROVE' AND NOT EXISTS(
          SELECT 1 FROM auto_stock_lot_disposition p
          JOIN auto_stock_lot l ON l.id=p.lot_id
          WHERE p.id=NEW.disposition_id AND p.lot_id=d.lot_id
            AND l.organization_id=d.organization_id AND l.location_id=d.location_id
            AND p.action='WRITE_OFF' AND p.quantity=d.quantity
            AND p.reason=d.note AND p.supplier_reference IS NULL
            AND p.request_id=NEW.request_id
        ) THEN RAISE EXCEPTION 'Списание не соответствует акту'; END IF;
        RETURN NEW;
      END $f$
    """)
    op.execute("""
      CREATE TRIGGER auto_writeoff_decision_guard BEFORE INSERT ON auto_stock_writeoff_decision
      FOR EACH ROW EXECUTE FUNCTION guard_auto_writeoff_decision()
    """)
    op.execute("REVOKE ALL ON auto_stock_writeoff_document,auto_stock_writeoff_decision FROM PUBLIC")


def downgrade():
    if op.get_bind().execute(text("SELECT 1 FROM auto_stock_writeoff_document LIMIT 1")).first():
        raise RuntimeError("История актов списания существует: требуется резервная копия")
    op.execute("DROP TABLE auto_stock_writeoff_decision")
    op.execute("DROP TABLE auto_stock_writeoff_document")
    op.execute("DROP FUNCTION guard_auto_writeoff_decision()")
    op.execute("DROP FUNCTION guard_auto_writeoff_document()")
