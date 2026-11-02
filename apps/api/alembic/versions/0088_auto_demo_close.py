"""Synthetic settlement ledger and immutable pre-handover preview; no real payments.

Revision ID: 0088_auto_demo_close
Revises: 0087_auto_execution
"""
from alembic import op

revision="0088_auto_demo_close"
down_revision="0087_auto_execution"
branch_labels=None
depends_on=None


def upgrade():
    op.execute("""
      CREATE TABLE auto_order_demo_cash_event (
        id uuid PRIMARY KEY,
        work_order_id uuid NOT NULL REFERENCES auto_work_order(id),
        revision_id uuid NOT NULL REFERENCES auto_work_order_estimate_revision(id),
        actor_id uuid NOT NULL REFERENCES app_user(id),
        request_key uuid NOT NULL UNIQUE,
        kind text NOT NULL CHECK(kind IN ('SIM_PAYMENT','SIM_REFUND')),
        amount_minor bigint NOT NULL CHECK(amount_minor BETWEEN 1 AND 1000000000000),
        created_at timestamptz NOT NULL DEFAULT clock_timestamp()
      )
    """)
    op.execute("""
      CREATE TABLE auto_order_demo_handover_preview (
        id uuid PRIMARY KEY,
        work_order_id uuid NOT NULL UNIQUE REFERENCES auto_work_order(id),
        revision_id uuid NOT NULL REFERENCES auto_work_order_estimate_revision(id),
        actor_id uuid NOT NULL REFERENCES app_user(id),
        request_key uuid NOT NULL UNIQUE,
        fingerprint char(64) NOT NULL,
        snapshot jsonb NOT NULL CHECK(jsonb_typeof(snapshot)='object'),
        created_at timestamptz NOT NULL DEFAULT clock_timestamp()
      )
    """)
    op.execute("""
      CREATE FUNCTION guard_auto_demo_cash()
      RETURNS trigger LANGUAGE plpgsql AS $fn$
      DECLARE total_amount bigint; paid bigint; returned bigint;
      BEGIN
        SELECT r.total_minor INTO total_amount
        FROM auto_work_order_estimate_revision r
        JOIN auto_work_order_estimate_decision d
          ON d.revision_id=r.id AND d.decision='APPROVED'
        JOIN auto_work_order w ON w.id=r.work_order_id
        JOIN auto_work_order_execution e ON e.work_order_id=w.id
          AND e.revision_id=r.id
        JOIN auto_work_order_execution_finish f ON f.execution_id=e.id
        JOIN auto_appointment a ON a.id=w.appointment_id
        JOIN auto_vehicle_customer_link proof ON proof.id=a.customer_link_id
          AND proof.vehicle_id=a.vehicle_id AND proof.verified_at IS NOT NULL
          AND proof.revoked_at IS NULL
        JOIN LATERAL (
          SELECT COALESCE((SELECT res.new_booking_id FROM auto_appointment_reschedule res
            WHERE res.appointment_id=a.id ORDER BY res.created_at DESC,res.id DESC LIMIT 1),
            a.booking_id) AS current_booking
        ) last_booking ON TRUE
        JOIN auto_bay_booking bay ON bay.id=last_booking.current_booking
          AND bay.canceled_at IS NULL
        JOIN auto_organization o ON o.id=w.organization_id AND o.disabled_at IS NULL
        JOIN auto_service_location loc ON loc.id=w.location_id
          AND loc.organization_id=o.id AND loc.disabled_at IS NULL
        WHERE w.id=NEW.work_order_id AND r.id=NEW.revision_id
          AND a.organization_id=w.organization_id AND a.location_id=w.location_id
          AND bay.organization_id=w.organization_id AND bay.location_id=w.location_id
          AND EXISTS(
            SELECT 1 FROM auto_work_order_qc_event qc
            WHERE qc.work_order_id=w.id AND qc.result='PASS'
              AND qc.created_at>=f.created_at
              AND NOT EXISTS(
                SELECT 1 FROM auto_work_order_qc_event newer
                WHERE newer.work_order_id=w.id AND
                  (newer.created_at,newer.id)>(qc.created_at,qc.id)
              )
          )
          AND NOT EXISTS(
            SELECT 1 FROM auto_order_demo_handover_preview doc
            WHERE doc.work_order_id=w.id
          )
        FOR UPDATE OF w;
        IF NOT FOUND THEN RAISE EXCEPTION 'demo settlement requires live QC-approved order'; END IF;
        SELECT COALESCE(SUM(amount_minor) FILTER(WHERE kind='SIM_PAYMENT'),0),
               COALESCE(SUM(amount_minor) FILTER(WHERE kind='SIM_REFUND'),0)
          INTO paid,returned
        FROM auto_order_demo_cash_event WHERE work_order_id=NEW.work_order_id;
        IF (NEW.kind='SIM_PAYMENT' AND paid+NEW.amount_minor-returned>total_amount)
           OR (NEW.kind='SIM_REFUND' AND returned+NEW.amount_minor>paid)
        THEN RAISE EXCEPTION 'demo settlement amount exceeds approved bound'; END IF;
        RETURN NEW;
      END $fn$
    """)
    op.execute("""
      CREATE TRIGGER auto_demo_cash_guard BEFORE INSERT ON auto_order_demo_cash_event
      FOR EACH ROW EXECUTE FUNCTION guard_auto_demo_cash()
    """)
    op.execute("""
      CREATE FUNCTION guard_auto_demo_preview()
      RETURNS trigger LANGUAGE plpgsql AS $fn$
      DECLARE total_amount bigint; paid bigint; returned bigint;
      BEGIN
        SELECT r.total_minor INTO total_amount
        FROM auto_work_order_estimate_revision r
        JOIN auto_work_order_estimate_decision d
          ON d.revision_id=r.id AND d.decision='APPROVED'
        JOIN auto_work_order_execution e ON e.work_order_id=r.work_order_id
          AND e.revision_id=r.id
        JOIN auto_work_order_execution_finish f ON f.execution_id=e.id
        JOIN auto_work_order w ON w.id=e.work_order_id
        WHERE r.id=NEW.revision_id AND w.id=NEW.work_order_id
          AND EXISTS(
            SELECT 1 FROM auto_work_order_qc_event qc
            WHERE qc.work_order_id=w.id AND qc.result='PASS'
              AND qc.created_at>=f.created_at
              AND NOT EXISTS(
                SELECT 1 FROM auto_work_order_qc_event newer
                WHERE newer.work_order_id=w.id
                  AND (newer.created_at,newer.id)>(qc.created_at,qc.id)
              )
          )
        FOR UPDATE OF w;
        IF NOT FOUND THEN RAISE EXCEPTION 'QC required for pre-handover preview'; END IF;
        SELECT COALESCE(SUM(amount_minor) FILTER(WHERE kind='SIM_PAYMENT'),0),
               COALESCE(SUM(amount_minor) FILTER(WHERE kind='SIM_REFUND'),0)
        INTO paid,returned FROM auto_order_demo_cash_event
        WHERE work_order_id=NEW.work_order_id;
        IF paid-returned<>total_amount THEN
          RAISE EXCEPTION 'demo settlement not reconciled'; END IF;
        IF EXISTS(
          SELECT 1 FROM auto_stock_lot_work_order_issue i
          WHERE i.work_order_id=NEW.work_order_id
            AND NOT EXISTS(SELECT 1 FROM auto_stock_mechanic_handoff h WHERE h.issue_id=i.id)
        ) OR EXISTS(
          SELECT 1 FROM auto_work_order_stock_event e
          WHERE e.work_order_id=NEW.work_order_id
          GROUP BY e.sku_id
          HAVING SUM(CASE WHEN action='RESERVE' THEN quantity
                          WHEN action IN ('ISSUE','RELEASE') THEN -quantity
                          ELSE 0 END)<>0
        ) THEN RAISE EXCEPTION 'stock reservations or handoffs remain'; END IF;
        RETURN NEW;
      END $fn$
    """)
    op.execute("""
      CREATE TRIGGER auto_demo_preview_guard BEFORE INSERT
      ON auto_order_demo_handover_preview FOR EACH ROW
      EXECUTE FUNCTION guard_auto_demo_preview()
    """)
    op.execute("""
      CREATE FUNCTION guard_auto_demo_stock_freeze()
      RETURNS trigger LANGUAGE plpgsql AS $fn$
      BEGIN
        IF EXISTS(SELECT 1 FROM auto_order_demo_handover_preview d
                  WHERE d.work_order_id=NEW.work_order_id)
        THEN RAISE EXCEPTION 'demo preview sealed; stock cannot change'; END IF;
        RETURN NEW;
      END $fn$
    """)
    op.execute("""
      CREATE TRIGGER auto_demo_stock_freeze BEFORE INSERT ON auto_work_order_stock_event
      FOR EACH ROW EXECUTE FUNCTION guard_auto_demo_stock_freeze()
    """)
    for table in ("auto_order_demo_cash_event","auto_order_demo_handover_preview"):
        op.execute(f"""CREATE TRIGGER {table}_immutable
          BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW
          EXECUTE FUNCTION immutable_auto_stock_event()""")
        op.execute(f"REVOKE ALL ON {table} FROM PUBLIC")


def downgrade():
    # Only on disposable synthetic database. Audit/legal approval not granted.
    op.execute("DROP TRIGGER auto_demo_stock_freeze ON auto_work_order_stock_event")
    op.execute("DROP FUNCTION guard_auto_demo_stock_freeze()")
    op.execute("DROP TABLE auto_order_demo_handover_preview")
    op.execute("DROP TABLE auto_order_demo_cash_event")
    op.execute("DROP FUNCTION guard_auto_demo_preview()")
    op.execute("DROP FUNCTION guard_auto_demo_cash()")
