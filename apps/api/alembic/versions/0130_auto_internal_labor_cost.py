"""Неизменяемая внутренняя расчётная ставка филиала, не зарплата сотрудника.

Revision ID: 0130_auto_internal_labor_cost
Revises: 0129_auto_report_provenance
"""
from alembic import op

revision = "0130_auto_internal_labor_cost"
down_revision = "0129_auto_report_provenance"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""ALTER TABLE auto_service_location ADD COLUMN internal_cost_epoch bigint
      NOT NULL DEFAULT 0 CHECK(internal_cost_epoch>=0)""")
    op.execute("""
      CREATE TABLE auto_internal_labor_cost_revision (
        id uuid PRIMARY KEY,
        organization_id uuid NOT NULL REFERENCES auto_organization(id),
        location_id uuid NOT NULL REFERENCES auto_service_location(id),
        version integer NOT NULL CHECK(version BETWEEN 1 AND 100),
        rate_minor_per_hour bigint NOT NULL CHECK(rate_minor_per_hour BETWEEN 0 AND 10000000000),
        actor_id uuid NOT NULL REFERENCES app_user(id),
        request_key uuid NOT NULL UNIQUE,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        UNIQUE(location_id,version)
      )
    """)
    op.execute("""
      CREATE TABLE auto_internal_labor_cost_approval (
        id uuid PRIMARY KEY,
        revision_id uuid NOT NULL UNIQUE REFERENCES auto_internal_labor_cost_revision(id),
        approved_by uuid NOT NULL REFERENCES app_user(id),
        request_key uuid NOT NULL UNIQUE,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp()
      )
    """)
    op.execute("""
      CREATE FUNCTION guard_auto_internal_labor_cost() RETURNS trigger
      LANGUAGE plpgsql AS $f$
      DECLARE scope_org uuid; scope_loc uuid; actor uuid;
              proposal auto_internal_labor_cost_revision%ROWTYPE;
      BEGIN
        IF TG_TABLE_NAME='auto_internal_labor_cost_revision' THEN
          scope_org:=NEW.organization_id; scope_loc:=NEW.location_id;
          actor:=NEW.actor_id;
        ELSE
          SELECT * INTO proposal FROM auto_internal_labor_cost_revision
            WHERE id=NEW.revision_id;
          IF NOT FOUND OR NEW.approved_by=proposal.actor_id THEN
            RAISE EXCEPTION 'independent cost approval required';
          END IF;
          scope_org:=proposal.organization_id; scope_loc:=proposal.location_id;
          actor:=NEW.approved_by;
        END IF;
        -- Такой же порядок блокировок, как при публикации через сервис.
        PERFORM 1 FROM auto_organization WHERE id=scope_org AND disabled_at IS NULL FOR SHARE;
        IF NOT FOUND THEN RAISE EXCEPTION 'inactive cost organization'; END IF;
        PERFORM 1 FROM auto_service_location WHERE id=scope_loc
          AND organization_id=scope_org AND disabled_at IS NULL FOR UPDATE;
        IF NOT FOUND THEN RAISE EXCEPTION 'inactive cost location'; END IF;
        PERFORM 1 FROM auto_organization_role_grant g
          JOIN app_user u ON u.id=g.user_id AND u.status='ACTIVE'
          WHERE g.organization_id=scope_org AND g.user_id=actor
            AND g.role_code='MANAGER' AND g.revoked_at IS NULL
            AND (g.location_id=scope_loc OR g.location_id IS NULL)
          FOR SHARE OF g,u;
        IF NOT FOUND THEN RAISE EXCEPTION 'cost manager authority required'; END IF;
        IF TG_TABLE_NAME='auto_internal_labor_cost_revision' THEN
          IF NEW.version<>1+COALESCE((SELECT max(version)
            FROM auto_internal_labor_cost_revision WHERE location_id=scope_loc),0)
            OR EXISTS (
              SELECT 1 FROM auto_internal_labor_cost_revision r
              LEFT JOIN auto_internal_labor_cost_approval a ON a.revision_id=r.id
              WHERE r.location_id=scope_loc AND a.id IS NULL
            ) THEN RAISE EXCEPTION 'cost policy version conflict'; END IF;
        ELSE
          IF EXISTS(SELECT 1 FROM auto_internal_labor_cost_revision r
            WHERE r.location_id=scope_loc AND r.version>proposal.version)
          THEN RAISE EXCEPTION 'stale cost proposal'; END IF;
          -- Фактическая MVCC-версия branch для RR-транзакции труда.
          UPDATE auto_service_location SET internal_cost_epoch=internal_cost_epoch+1
            WHERE id=scope_loc;
        END IF;
        -- Запрет подмены периода действия ставкой, опубликованной задним числом.
        NEW.created_at:=clock_timestamp();
        RETURN NEW;
      END $f$
    """)
    for table in ("auto_internal_labor_cost_revision","auto_internal_labor_cost_approval"):
        op.execute(f"""CREATE TRIGGER {table}_guard BEFORE INSERT ON {table}
          FOR EACH ROW EXECUTE FUNCTION guard_auto_internal_labor_cost()""")
        op.execute(f"""CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table}
          FOR EACH ROW EXECUTE FUNCTION immutable_auto_stock_event()""")
        op.execute(f"REVOKE ALL ON {table} FROM PUBLIC")
    op.execute("""
      ALTER TABLE auto_work_order_labor_entry ADD COLUMN internal_cost_approval_id uuid
        REFERENCES auto_internal_labor_cost_approval(id),
        ADD COLUMN internal_cost_registered_at timestamptz
    """)
    op.execute("""
      CREATE FUNCTION snapshot_auto_internal_labor_cost() RETURNS trigger
      LANGUAGE plpgsql AS $f$
      DECLARE scope_org uuid; scope_loc uuid; selected uuid;
      BEGIN
        SELECT e.organization_id,e.location_id INTO scope_org,scope_loc
          FROM auto_work_order_execution e WHERE e.id=NEW.execution_id;
        IF NOT FOUND THEN RAISE EXCEPTION 'execution unavailable'; END IF;
        PERFORM 1 FROM auto_organization WHERE id=scope_org AND disabled_at IS NULL FOR SHARE;
        IF NOT FOUND THEN RAISE EXCEPTION 'inactive cost organization'; END IF;
        PERFORM 1 FROM auto_service_location WHERE id=scope_loc
          AND organization_id=scope_org AND disabled_at IS NULL FOR SHARE;
        IF NOT FOUND THEN RAISE EXCEPTION 'inactive cost location'; END IF;
        NEW.internal_cost_registered_at:=clock_timestamp();
        SELECT a.id INTO selected
          FROM auto_internal_labor_cost_revision r
          JOIN auto_internal_labor_cost_approval a ON a.revision_id=r.id
          WHERE r.organization_id=scope_org AND r.location_id=scope_loc
            AND a.created_at<=NEW.internal_cost_registered_at
          ORDER BY r.version DESC LIMIT 1;
        IF NEW.internal_cost_approval_id IS NOT NULL
           AND NEW.internal_cost_approval_id IS DISTINCT FROM selected
        THEN RAISE EXCEPTION 'forged internal labor cost snapshot'; END IF;
        NEW.internal_cost_approval_id:=selected;
        RETURN NEW;
      END $f$
    """)
    op.execute("""
      CREATE TRIGGER auto_internal_labor_cost_snapshot BEFORE INSERT
      ON auto_work_order_labor_entry FOR EACH ROW
      EXECUTE FUNCTION snapshot_auto_internal_labor_cost()
    """)


def downgrade():
    # Только disposable PG17; удаляется расчётная история, не production.
    op.execute("DROP TRIGGER auto_internal_labor_cost_snapshot ON auto_work_order_labor_entry")
    op.execute("DROP FUNCTION snapshot_auto_internal_labor_cost()")
    op.execute("ALTER TABLE auto_work_order_labor_entry DROP COLUMN internal_cost_approval_id")
    op.execute("ALTER TABLE auto_work_order_labor_entry DROP COLUMN internal_cost_registered_at")
    op.execute("DROP TABLE auto_internal_labor_cost_approval")
    op.execute("DROP TABLE auto_internal_labor_cost_revision")
    op.execute("DROP FUNCTION guard_auto_internal_labor_cost()")
    op.execute("ALTER TABLE auto_service_location DROP COLUMN internal_cost_epoch")
