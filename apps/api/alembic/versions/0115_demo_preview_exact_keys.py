"""Точное множество ключей технического пакета; запрещён обход nullable-полей.

Revision ID: 0115_demo_preview_exact_keys
Revises: 0114_auto_demo_preview_integrity
Синтетическая миграция: не разрешает фактическую выдачу или оплату.
"""
from alembic import op

revision = "0115_demo_preview_exact_keys"
down_revision = "0114_auto_demo_preview_integrity"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
      CREATE FUNCTION guard_auto_demo_preview_exact_keys()
      RETURNS trigger LANGUAGE plpgsql AS $f$
      BEGIN
        -- Даже поля с JSON null должны присутствовать: нельзя заменить
        -- nullable поле произвольным признаком выдачи и пересчитать SHA.
        IF NOT (NEW.snapshot ?& ARRAY[
          'kind', 'workOrderId', 'estimateRevisionId', 'estimateVersion',
          'estimateDigest', 'currency', 'lines', 'approvedBaseMinor',
          'approvedExtraMinor', 'extraRevisionId', 'extraVersion',
          'extraDigest', 'extraLines', 'approvedTotalMinor',
          'simulatedPaidMinor', 'simulatedRefundedMinor', 'simulatedDueMinor',
          'independentQc', 'stockReservations', 'unreceivedMaterials',
          'legalGatePassed', 'releaseAllowed'
        ]) OR (SELECT count(*) FROM jsonb_object_keys(NEW.snapshot))<>22
        THEN RAISE EXCEPTION 'preview key set mismatch'; END IF;
        RETURN NEW;
      END $f$
    """)
    op.execute("""
      CREATE TRIGGER zzz_auto_demo_preview_exact_keys
      BEFORE INSERT ON auto_order_demo_handover_preview
      FOR EACH ROW EXECUTE FUNCTION guard_auto_demo_preview_exact_keys()
    """)


def downgrade():
    # Только disposable PG17, существующие immutable preview сохраняются.
    op.execute("DROP TRIGGER zzz_auto_demo_preview_exact_keys ON auto_order_demo_handover_preview")
    op.execute("DROP FUNCTION guard_auto_demo_preview_exact_keys()")
