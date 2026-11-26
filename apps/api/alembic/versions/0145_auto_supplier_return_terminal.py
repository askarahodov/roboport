"""Единственное терминальное решение по акту возврата поставщику (#168)."""
from alembic import op
from sqlalchemy import text

revision = "0145_auto_return_terminal"
down_revision = "0144_auto_booking_active_staff"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
      CREATE TABLE auto_stock_supplier_return_terminal (
        id uuid PRIMARY KEY,
        request_id uuid NOT NULL UNIQUE,
        document_id uuid NOT NULL UNIQUE REFERENCES auto_stock_supplier_return_document(id),
        action text NOT NULL CHECK(action IN ('APPROVE','CANCEL','REJECT')),
        note text,
        actor_id uuid NOT NULL REFERENCES app_user(id),
        approval_id uuid UNIQUE REFERENCES auto_stock_supplier_return_acceptance(id),
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        CHECK (
          (action='APPROVE' AND approval_id IS NOT NULL AND note IS NULL)
          OR (action IN ('CANCEL','REJECT') AND approval_id IS NULL
              AND note IS NOT NULL AND note=btrim(note) AND length(note) BETWEEN 3 AND 240)
        )
      )
    """)
    # Исторические утверждения сохраняются с исходными идентификаторами и временем.
    op.execute("""
      INSERT INTO auto_stock_supplier_return_terminal
        (id,request_id,document_id,action,note,actor_id,approval_id,created_at)
      SELECT id,request_id,document_id,'APPROVE',NULL,accepted_by,id,created_at
      FROM auto_stock_supplier_return_acceptance
    """)
    op.execute("""
      CREATE FUNCTION guard_auto_supplier_return_terminal() RETURNS trigger
      LANGUAGE plpgsql AS $fn$
      DECLARE d record; actor uuid;
      BEGIN
        SELECT doc.created_by,doc.sku_id INTO d
        FROM auto_stock_supplier_return_document doc
        JOIN auto_service_location l ON l.id=doc.location_id
          AND l.organization_id=doc.organization_id
        JOIN auto_organization o ON o.id=doc.organization_id
        WHERE doc.id=NEW.document_id AND l.disabled_at IS NULL AND o.disabled_at IS NULL
        FOR SHARE OF doc,l,o;
        IF NOT FOUND THEN RAISE EXCEPTION 'Акт возврата недоступен'; END IF;
        -- Один ключ сериализации с утверждением и физическим ledger.
        PERFORM 1 FROM auto_stock_sku WHERE id=d.sku_id FOR UPDATE;
        IF NEW.action='APPROVE' THEN
          IF NOT EXISTS (
            SELECT 1 FROM auto_stock_supplier_return_acceptance a
            WHERE a.id=NEW.approval_id AND a.id=NEW.id
              AND a.document_id=NEW.document_id AND a.request_id=NEW.request_id
              AND a.accepted_by=NEW.actor_id AND a.created_at=NEW.created_at
          ) THEN RAISE EXCEPTION 'Решение не соответствует утверждению'; END IF;
        ELSE
          actor:=NULLIF(current_setting('roboport.supplier_return_actor',true),'')::uuid;
          IF actor IS NULL OR NEW.actor_id IS DISTINCT FROM actor
            OR ((NEW.action='CANCEL')<>(actor=d.created_by))
          THEN RAISE EXCEPTION 'Требуется отмена автором либо независимый отказ'; END IF;
          NEW.created_at:=clock_timestamp();
          IF EXISTS (SELECT 1 FROM auto_stock_supplier_return_acceptance
                     WHERE document_id=NEW.document_id)
          THEN RAISE EXCEPTION 'Возврат уже утверждён'; END IF;
        END IF;
        RETURN NEW;
      END $fn$
    """)
    op.execute("""
      CREATE TRIGGER auto_supplier_return_terminal_guard BEFORE INSERT
      ON auto_stock_supplier_return_terminal FOR EACH ROW
      EXECUTE FUNCTION guard_auto_supplier_return_terminal()
    """)
    op.execute("""
      CREATE TRIGGER auto_supplier_return_terminal_immutable BEFORE UPDATE OR DELETE
      ON auto_stock_supplier_return_terminal FOR EACH ROW
      EXECUTE FUNCTION immutable_auto_stock_event()
    """)
    op.execute("""
      CREATE FUNCTION record_auto_supplier_return_terminal_approval() RETURNS trigger
      LANGUAGE plpgsql AS $fn$
      BEGIN
        -- UNIQUE(document_id) исключает два терминальных решения даже при
        -- конкурирующих транзакциях; конфликт откатывает также физические проводки.
        INSERT INTO auto_stock_supplier_return_terminal
          (id,request_id,document_id,action,note,actor_id,approval_id,created_at)
        VALUES(NEW.id,NEW.request_id,NEW.document_id,'APPROVE',NULL,
               NEW.accepted_by,NEW.id,NEW.created_at);
        RETURN NULL;
      END $fn$
    """)
    op.execute("""
      CREATE TRIGGER auto_supplier_return_terminal_approval AFTER INSERT
      ON auto_stock_supplier_return_acceptance FOR EACH ROW
      EXECUTE FUNCTION record_auto_supplier_return_terminal_approval()
    """)
    op.execute("REVOKE ALL ON auto_stock_supplier_return_terminal FROM PUBLIC")


def downgrade():
    if op.get_bind().execute(text("""
        SELECT 1 FROM auto_stock_supplier_return_terminal
        WHERE action<>'APPROVE' LIMIT 1
    """)).first():
        raise RuntimeError("История отмен и отказов существует: требуется согласованный rollback")
    op.execute("DROP TRIGGER auto_supplier_return_terminal_approval ON auto_stock_supplier_return_acceptance")
    op.execute("DROP FUNCTION record_auto_supplier_return_terminal_approval()")
    op.execute("DROP TABLE auto_stock_supplier_return_terminal")
    op.execute("DROP FUNCTION guard_auto_supplier_return_terminal()")
