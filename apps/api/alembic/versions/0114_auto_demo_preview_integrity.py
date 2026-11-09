"""Неизменяемая техническая первичка: проверка происхождения и хеша в PG17.

Revision ID: 0114_auto_demo_preview_integrity
Revises: 0113_auto_extra_settlement
Только синтетический контур. Не является юридическим проведением или выдачей.
"""
from alembic import op

revision = "0114_auto_demo_preview_integrity"
down_revision = "0113_auto_extra_settlement"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
      CREATE FUNCTION guard_auto_demo_preview_integrity()
      RETURNS trigger LANGUAGE plpgsql AS $f$
      DECLARE digest text;
      BEGIN
        -- Технический документ всегда связан с реальным автором и ролью
        -- MANAGER именно в активном филиале/организации исходного WorkOrder.
        IF NOT EXISTS (
          SELECT 1 FROM auto_work_order w
          JOIN auto_organization_role_grant g
            ON g.organization_id=w.organization_id
            AND g.user_id=NEW.actor_id AND g.role_code='MANAGER'
            AND g.revoked_at IS NULL
            AND (g.location_id=w.location_id OR g.location_id IS NULL)
          WHERE w.id=NEW.work_order_id
          FOR SHARE OF w,g
        ) THEN RAISE EXCEPTION 'preview actor lacks live MANAGER grant'; END IF;

        -- Прямой SQL не может добавить произвольные поля, скрытые статусы
        -- выдачи или иную валюту в экспортируемый клиенту JSON-документ.
        IF (SELECT count(*) FROM jsonb_object_keys(NEW.snapshot))<>22
          OR NEW.snapshot->>'kind'
             IS DISTINCT FROM 'SYNTHETIC_PRE_HANDOVER_PREVIEW'
          OR NEW.snapshot->>'workOrderId'
             IS DISTINCT FROM NEW.work_order_id::text
          OR NEW.snapshot->>'currency' IS DISTINCT FROM 'RUB'
          OR NEW.snapshot->>'independentQc' IS DISTINCT FROM 'PASS'
          OR NEW.snapshot->>'stockReservations' IS DISTINCT FROM '0'
          OR NEW.snapshot->>'unreceivedMaterials' IS DISTINCT FROM '0'
          OR jsonb_typeof(NEW.snapshot->'stockReservations')<>'number'
          OR jsonb_typeof(NEW.snapshot->'unreceivedMaterials')<>'number'
          OR NEW.snapshot ? 'documentPosted'
          OR NEW.snapshot ? 'deliveredAt'
          OR NEW.snapshot ? 'legalSignature'
        THEN RAISE EXCEPTION 'invalid synthetic preview metadata'; END IF;

        digest:=encode(sha256(convert_to(NEW.snapshot::text,'UTF8')),'hex');
        IF btrim(NEW.fingerprint) IS DISTINCT FROM digest
        THEN RAISE EXCEPTION 'preview fingerprint mismatch'; END IF;
        RETURN NEW;
      END $f$
    """)
    op.execute("""
      CREATE TRIGGER zz_auto_demo_preview_integrity
      BEFORE INSERT ON auto_order_demo_handover_preview
      FOR EACH ROW EXECUTE FUNCTION guard_auto_demo_preview_integrity()
    """)


def downgrade():
    # Только одноразовая тестовая БД; immutable версии не удаляются.
    op.execute("DROP TRIGGER zz_auto_demo_preview_integrity ON auto_order_demo_handover_preview")
    op.execute("DROP FUNCTION guard_auto_demo_preview_integrity()")
