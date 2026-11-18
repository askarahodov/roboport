"""Порядок первичных событий и fence публикации ACL для согласованных отчётов.

Revision ID: 0129_auto_report_provenance
Revises: 0128_auto_policy_history
Старые записи не получают выдуманный порядок задним числом.
"""
from alembic import op

revision = "0129_auto_report_provenance"
down_revision = "0128_auto_policy_history"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
      ALTER TABLE auto_organization ADD COLUMN document_policy_epoch bigint
        NOT NULL DEFAULT 0 CHECK(document_policy_epoch>=0)
    """)
    op.execute("""
      CREATE FUNCTION advance_auto_document_policy_epoch() RETURNS trigger
      LANGUAGE plpgsql AS $f$
      BEGIN
        -- Реальная новая MVCC-версия org, а не только блокировка.
        -- RR-читатель старого снимка после публикации получит 40001.
        UPDATE auto_organization SET document_policy_epoch=document_policy_epoch+1
          WHERE id=NEW.organization_id AND disabled_at IS NULL;
        IF NOT FOUND THEN RAISE EXCEPTION 'inactive policy organization'; END IF;
        RETURN NEW;
      END $f$
    """)
    op.execute("""
      CREATE TRIGGER aa_auto_document_policy_epoch BEFORE INSERT
      ON auto_document_access_policy FOR EACH ROW
      EXECUTE FUNCTION advance_auto_document_policy_epoch()
    """)
    op.execute("CREATE SEQUENCE auto_cost_registration_seq AS bigint NO CYCLE")
    for table in ("auto_stock_lot_allocation","auto_stock_receipt_valuation"):
        op.execute(f"""ALTER TABLE {table} ADD COLUMN cost_registration_seq bigint
          CHECK(cost_registration_seq>0)""")
    op.execute("""
      CREATE FUNCTION stamp_auto_cost_registration() RETURNS trigger
      LANGUAGE plpgsql AS $f$
      DECLARE item uuid;
      BEGIN
        IF TG_TABLE_NAME='auto_stock_lot_allocation' THEN
          item:=NEW.sku_id;
        ELSE
          SELECT sku_id INTO item FROM auto_stock_receipt_document
            WHERE id=NEW.document_id;
        END IF;
        -- Общий SKU mutex выдачи, распределения, прихода и оценки.
        -- Номер берётся только после блокировки, не из now()/начала транзакции.
        PERFORM 1 FROM auto_stock_sku WHERE id=item FOR UPDATE;
        IF NOT FOUND THEN RAISE EXCEPTION 'cost registration SKU unavailable'; END IF;
        NEW.cost_registration_seq:=nextval('auto_cost_registration_seq');
        RETURN NEW;
      END $f$
    """)
    for table in ("auto_stock_lot_allocation","auto_stock_receipt_valuation"):
        op.execute(f"""CREATE TRIGGER zz_{table}_cost_registration BEFORE INSERT
          ON {table} FOR EACH ROW EXECUTE FUNCTION stamp_auto_cost_registration()""")


def downgrade():
    # Только одноразовый synthetic PG17, без изменения самих первичных событий.
    for table in ("auto_stock_lot_allocation","auto_stock_receipt_valuation"):
        op.execute(f"DROP TRIGGER zz_{table}_cost_registration ON {table}")
        op.execute(f"ALTER TABLE {table} DROP COLUMN cost_registration_seq")
    op.execute("DROP FUNCTION stamp_auto_cost_registration()")
    op.execute("DROP SEQUENCE auto_cost_registration_seq")
    op.execute("DROP TRIGGER aa_auto_document_policy_epoch ON auto_document_access_policy")
    op.execute("DROP FUNCTION advance_auto_document_policy_epoch()")
    op.execute("ALTER TABLE auto_organization DROP COLUMN document_policy_epoch")
