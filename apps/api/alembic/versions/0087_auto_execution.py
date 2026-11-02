"""Approved automotive execution: immutable start, labor and finish evidence.

Revision ID: 0087_auto_execution
Revises: 0086_auto_estimate
"""
from alembic import op

revision="0087_auto_execution"
down_revision="0086_auto_estimate"
branch_labels=None
depends_on=None


def upgrade():
    op.execute("""
      CREATE TABLE auto_work_order_execution (
        id uuid PRIMARY KEY,
        work_order_id uuid NOT NULL UNIQUE REFERENCES auto_work_order(id),
        revision_id uuid NOT NULL REFERENCES auto_work_order_estimate_revision(id),
        organization_id uuid NOT NULL REFERENCES auto_organization(id),
        location_id uuid NOT NULL REFERENCES auto_service_location(id),
        actor_id uuid NOT NULL REFERENCES app_user(id),
        request_key uuid NOT NULL UNIQUE,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp()
      )
    """)
    op.execute("""
      CREATE TABLE auto_work_order_labor_entry (
        id uuid PRIMARY KEY,
        execution_id uuid NOT NULL REFERENCES auto_work_order_execution(id),
        service_id uuid NOT NULL REFERENCES auto_service_catalog(id),
        actor_id uuid NOT NULL REFERENCES app_user(id),
        actual_minutes integer NOT NULL CHECK(actual_minutes BETWEEN 1 AND 1440),
        request_key uuid NOT NULL UNIQUE,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        UNIQUE(execution_id,service_id)
      )
    """)
    op.execute("""
      CREATE TABLE auto_work_order_execution_finish (
        id uuid PRIMARY KEY,
        execution_id uuid NOT NULL UNIQUE REFERENCES auto_work_order_execution(id),
        actor_id uuid NOT NULL REFERENCES app_user(id),
        request_key uuid NOT NULL UNIQUE,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp()
      )
    """)
    op.execute("""
      CREATE FUNCTION guard_auto_execution_start()
      RETURNS trigger LANGUAGE plpgsql AS $f$
      BEGIN
        IF NOT EXISTS (
          SELECT 1 FROM auto_work_order_estimate_revision r
          JOIN auto_work_order_estimate_decision d ON d.revision_id=r.id
            AND d.decision='APPROVED'
          JOIN auto_work_order w ON w.id=r.work_order_id
          JOIN auto_work_order_inspection inspection ON inspection.work_order_id=w.id
          WHERE r.id=NEW.revision_id AND r.work_order_id=NEW.work_order_id
            AND w.organization_id=NEW.organization_id
            AND w.location_id=NEW.location_id
            AND NOT EXISTS(SELECT 1 FROM auto_work_order_estimate_revision newer
              WHERE newer.work_order_id=w.id AND newer.version>r.version)
          FOR SHARE OF w,r,d,inspection
        ) THEN RAISE EXCEPTION 'execution requires approved current estimate and inspection'; END IF;
        RETURN NEW;
      END $f$
    """)
    op.execute("""
      CREATE TRIGGER auto_execution_start_guard
      BEFORE INSERT ON auto_work_order_execution FOR EACH ROW
      EXECUTE FUNCTION guard_auto_execution_start()
    """)
    op.execute("""
      CREATE FUNCTION guard_auto_execution_labor()
      RETURNS trigger LANGUAGE plpgsql AS $f$
      BEGIN
        IF NOT EXISTS(
          SELECT 1 FROM auto_work_order_execution e
          JOIN auto_work_order_estimate_revision r ON r.id=e.revision_id
          WHERE e.id=NEW.execution_id
            AND r.service_lines @> jsonb_build_array(jsonb_build_object(
                'serviceId', NEW.service_id::text))
            AND NOT EXISTS(SELECT 1 FROM auto_work_order_execution_finish f
                           WHERE f.execution_id=e.id)
          FOR SHARE OF e,r
        ) THEN RAISE EXCEPTION 'labor service not approved or execution finished'; END IF;
        RETURN NEW;
      END $f$
    """)
    op.execute("""
      CREATE TRIGGER auto_execution_labor_guard
      BEFORE INSERT ON auto_work_order_labor_entry FOR EACH ROW
      EXECUTE FUNCTION guard_auto_execution_labor()
    """)
    op.execute("""
      CREATE FUNCTION guard_auto_execution_finish()
      RETURNS trigger LANGUAGE plpgsql AS $f$
      BEGIN
        IF NOT EXISTS(
          SELECT 1 FROM auto_work_order_execution e
          JOIN auto_work_order_estimate_revision r ON r.id=e.revision_id
          WHERE e.id=NEW.execution_id
            AND (SELECT count(*) FROM auto_work_order_labor_entry l
                 WHERE l.execution_id=e.id)=jsonb_array_length(r.service_lines)
            AND NOT EXISTS (
              SELECT 1 FROM auto_stock_lot_work_order_issue i
              WHERE i.work_order_id=e.work_order_id
                AND NOT EXISTS (SELECT 1 FROM auto_stock_mechanic_handoff h
                                WHERE h.issue_id=i.id)
            )
          FOR SHARE OF e,r
        ) THEN RAISE EXCEPTION 'approved labor or stock handoff incomplete'; END IF;
        RETURN NEW;
      END $f$
    """)
    op.execute("""
      CREATE TRIGGER auto_execution_finish_guard
      BEFORE INSERT ON auto_work_order_execution_finish FOR EACH ROW
      EXECUTE FUNCTION guard_auto_execution_finish()
    """)
    op.execute("""
      CREATE FUNCTION guard_auto_execution_qc()
      RETURNS trigger LANGUAGE plpgsql AS $f$
      BEGIN
        IF EXISTS(SELECT 1 FROM auto_work_order_execution
                  WHERE work_order_id=NEW.work_order_id) AND (
           NOT EXISTS(
             SELECT 1 FROM auto_work_order_execution_finish f
             JOIN auto_work_order_execution e ON e.id=f.execution_id
             WHERE e.work_order_id=NEW.work_order_id)
           OR EXISTS(
             SELECT 1 FROM auto_work_order_labor_entry l
             JOIN auto_work_order_execution e ON e.id=l.execution_id
             WHERE e.work_order_id=NEW.work_order_id AND l.actor_id=NEW.actor_id)
        ) THEN RAISE EXCEPTION 'execution must finish and QC actor must be independent'; END IF;
        RETURN NEW;
      END $f$
    """)
    op.execute("""
      CREATE TRIGGER auto_execution_qc_guard BEFORE INSERT ON auto_work_order_qc_event
      FOR EACH ROW EXECUTE FUNCTION guard_auto_execution_qc()
    """)
    op.execute("""
      CREATE FUNCTION guard_auto_execution_late_issue()
      RETURNS trigger LANGUAGE plpgsql AS $f$
      BEGIN
        IF EXISTS(
          SELECT 1 FROM auto_work_order_execution_finish f
          JOIN auto_work_order_execution e ON e.id=f.execution_id
          WHERE e.work_order_id=NEW.work_order_id
        ) THEN RAISE EXCEPTION 'materials forbidden after execution finish'; END IF;
        RETURN NEW;
      END $f$
    """)
    op.execute("""
      CREATE TRIGGER auto_execution_late_issue_guard BEFORE INSERT
      ON auto_stock_lot_work_order_issue FOR EACH ROW
      EXECUTE FUNCTION guard_auto_execution_late_issue()
    """)
    for table in ("auto_work_order_execution","auto_work_order_labor_entry",
                  "auto_work_order_execution_finish"):
        op.execute(f"""CREATE TRIGGER {table}_immutable
          BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW
          EXECUTE FUNCTION immutable_auto_stock_event()""")
        op.execute(f"REVOKE ALL ON {table} FROM PUBLIC")


def downgrade():
    # Only for disposable synthetic DB: deleting audit evidence on real DB prohibited.
    op.execute("DROP TRIGGER auto_execution_qc_guard ON auto_work_order_qc_event")
    op.execute("DROP TRIGGER auto_execution_late_issue_guard ON auto_stock_lot_work_order_issue")
    op.execute("DROP FUNCTION guard_auto_execution_qc()")
    op.execute("DROP FUNCTION guard_auto_execution_late_issue()")
    for table in ("auto_work_order_execution_finish","auto_work_order_labor_entry",
                  "auto_work_order_execution"):
        op.execute(f"DROP TABLE {table}")
    for name in ("guard_auto_execution_finish","guard_auto_execution_labor",
                 "guard_auto_execution_start"):
        op.execute(f"DROP FUNCTION {name}()")
