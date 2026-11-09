"""Учитывать отдельно согласованные допработы в технической сверке и SQL-guards.

Revision ID: 0113_auto_extra_settlement
Revises: 0112_auto_intake_photo_dedupe
Только синтетический контур: не проводка денежных средств и не выдача ТС.
"""
from alembic import op

revision = "0113_auto_extra_settlement"
down_revision = "0112_auto_intake_photo_dedupe"
branch_labels = None
depends_on = None


def upgrade():
    # Сохраняем определения старых guards для точного обратимого downgrade.
    op.execute("""
      CREATE TABLE auto_demo_guard_restore (
        name text PRIMARY KEY,
        definition text NOT NULL
      )
    """)
    op.execute("""
      INSERT INTO auto_demo_guard_restore(name,definition)
      VALUES
      ('cash',pg_get_functiondef('guard_auto_demo_cash()'::regprocedure)),
      ('preview',pg_get_functiondef('guard_auto_demo_preview()'::regprocedure))
    """)
    op.execute("""
      CREATE FUNCTION auto_demo_billable_total(p_order uuid,p_revision uuid)
      RETURNS bigint LANGUAGE plpgsql STABLE AS $f$
      DECLARE base_total bigint; extra record;
      BEGIN
        SELECT r.total_minor INTO base_total
        FROM auto_work_order_estimate_revision r
        JOIN auto_work_order_estimate_decision d
          ON d.revision_id=r.id AND d.decision='APPROVED'
        WHERE r.id=p_revision AND r.work_order_id=p_order
          AND r.version=(SELECT max(latest.version)
                         FROM auto_work_order_estimate_revision latest
                         WHERE latest.work_order_id=p_order);
        IF NOT FOUND THEN RETURN NULL; END IF;
        SELECT x.id,x.base_revision_id,x.total_minor,d.decision INTO extra
        FROM auto_work_order_extra_revision x
        LEFT JOIN auto_work_order_extra_decision d ON d.revision_id=x.id
        WHERE x.work_order_id=p_order
        ORDER BY x.version DESC LIMIT 1;
        IF FOUND THEN
          -- Решения по прежним версиям не участвуют в расчёте.
          IF extra.decision IS NULL THEN RETURN NULL; END IF;
          IF extra.decision='APPROVED' THEN
            IF extra.base_revision_id<>p_revision THEN RETURN NULL; END IF;
            RETURN base_total+extra.total_minor;
          END IF;
        END IF;
        RETURN base_total;
      END $f$
    """)
    op.execute("""
      CREATE OR REPLACE FUNCTION guard_auto_demo_cash()
      RETURNS trigger LANGUAGE plpgsql AS $f$
      DECLARE total_amount bigint; paid bigint; refunded bigint;
      BEGIN
        IF NOT EXISTS(
          SELECT 1 FROM auto_work_order_estimate_revision r
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
            SELECT COALESCE((SELECT rs.new_booking_id
              FROM auto_appointment_reschedule rs WHERE rs.appointment_id=a.id
              ORDER BY rs.created_at DESC,rs.id DESC LIMIT 1),
              a.booking_id) AS current_booking
          ) booking ON TRUE
          JOIN auto_bay_booking b ON b.id=booking.current_booking
            AND b.canceled_at IS NULL
            AND b.organization_id=w.organization_id AND b.location_id=w.location_id
          JOIN auto_organization o ON o.id=w.organization_id AND o.disabled_at IS NULL
          JOIN auto_service_location loc ON loc.id=w.location_id
            AND loc.organization_id=o.id AND loc.disabled_at IS NULL
          WHERE w.id=NEW.work_order_id AND r.id=NEW.revision_id
            AND a.organization_id=w.organization_id AND a.location_id=w.location_id
            AND EXISTS(
              SELECT 1 FROM auto_work_order_qc_event qc
              WHERE qc.work_order_id=w.id AND qc.result='PASS'
                AND qc.created_at>=f.created_at
                AND NOT EXISTS(
                  SELECT 1 FROM auto_work_order_qc_event newer
                  WHERE newer.work_order_id=w.id
                    AND (newer.created_at,newer.id)>(qc.created_at,qc.id))
            )
            AND NOT EXISTS(
              SELECT 1 FROM auto_order_demo_handover_preview doc
              WHERE doc.work_order_id=w.id)
          FOR UPDATE OF w
        ) THEN RAISE EXCEPTION 'demo cash requires live QC-approved order'; END IF;
        total_amount:=auto_demo_billable_total(NEW.work_order_id,NEW.revision_id);
        IF total_amount IS NULL
        THEN RAISE EXCEPTION 'latest estimate and extra consent required'; END IF;
        SELECT COALESCE(sum(amount_minor) FILTER(WHERE kind='SIM_PAYMENT'),0),
               COALESCE(sum(amount_minor) FILTER(WHERE kind='SIM_REFUND'),0)
        INTO paid,refunded
        FROM auto_order_demo_cash_event WHERE work_order_id=NEW.work_order_id;
        IF (NEW.kind='SIM_PAYMENT' AND paid+NEW.amount_minor-refunded>total_amount)
           OR (NEW.kind='SIM_REFUND' AND refunded+NEW.amount_minor>paid)
        THEN RAISE EXCEPTION 'demo settlement exceeds approved total'; END IF;
        RETURN NEW;
      END $f$
    """)
    op.execute("""
      CREATE OR REPLACE FUNCTION guard_auto_demo_preview()
      RETURNS trigger LANGUAGE plpgsql AS $f$
      DECLARE base_total bigint; full_total bigint; paid bigint; refunded bigint;
      DECLARE base_digest text; base_version integer; base_lines jsonb; extra record;
      DECLARE extra_id uuid; extra_version integer; extra_digest text;
      DECLARE extra_amount bigint:=0; extra_lines jsonb:='[]'::jsonb;
      BEGIN
        SELECT r.total_minor,r.fingerprint,r.version,r.service_lines
        INTO base_total,base_digest,base_version,base_lines
        FROM auto_work_order_estimate_revision r
        JOIN auto_work_order_estimate_decision d
          ON d.revision_id=r.id AND d.decision='APPROVED'
        JOIN auto_work_order_execution e ON e.work_order_id=r.work_order_id
          AND e.revision_id=r.id
        JOIN auto_work_order_execution_finish f ON f.execution_id=e.id
        JOIN auto_work_order w ON w.id=e.work_order_id
        JOIN auto_appointment a ON a.id=w.appointment_id
        JOIN auto_vehicle_customer_link proof ON proof.id=a.customer_link_id
          AND proof.vehicle_id=a.vehicle_id AND proof.verified_at IS NOT NULL
          AND proof.revoked_at IS NULL
        JOIN LATERAL (
          SELECT COALESCE((SELECT rs.new_booking_id
            FROM auto_appointment_reschedule rs WHERE rs.appointment_id=a.id
            ORDER BY rs.created_at DESC,rs.id DESC LIMIT 1),
            a.booking_id) AS current_booking
        ) booking ON TRUE
        JOIN auto_bay_booking b ON b.id=booking.current_booking
          AND b.canceled_at IS NULL
          AND b.organization_id=w.organization_id AND b.location_id=w.location_id
        JOIN auto_organization o ON o.id=w.organization_id AND o.disabled_at IS NULL
        JOIN auto_service_location loc ON loc.id=w.location_id
          AND loc.organization_id=o.id AND loc.disabled_at IS NULL
        WHERE r.id=NEW.revision_id AND w.id=NEW.work_order_id
          AND r.version=(SELECT max(latest.version)
                         FROM auto_work_order_estimate_revision latest
                         WHERE latest.work_order_id=w.id)
          AND a.organization_id=w.organization_id AND a.location_id=w.location_id
          AND EXISTS(
            SELECT 1 FROM auto_work_order_qc_event qc
            WHERE qc.work_order_id=w.id AND qc.result='PASS'
              AND qc.created_at>=f.created_at
              AND NOT EXISTS(
                SELECT 1 FROM auto_work_order_qc_event newer
                WHERE newer.work_order_id=w.id
                  AND (newer.created_at,newer.id)>(qc.created_at,qc.id))
          )
        FOR UPDATE OF w;
        IF NOT FOUND THEN RAISE EXCEPTION 'QC and live scope required'; END IF;
        full_total:=auto_demo_billable_total(NEW.work_order_id,NEW.revision_id);
        IF full_total IS NULL THEN RAISE EXCEPTION 'extra consent pending'; END IF;
        SELECT x.id,x.version,x.fingerprint,x.total_minor,x.service_lines,d.decision
          INTO extra
        FROM auto_work_order_extra_revision x
        LEFT JOIN auto_work_order_extra_decision d ON d.revision_id=x.id
        WHERE x.work_order_id=NEW.work_order_id
        ORDER BY x.version DESC LIMIT 1;
        IF FOUND AND extra.decision='APPROVED' THEN
          extra_id:=extra.id;
          extra_version:=extra.version;
          extra_digest:=btrim(extra.fingerprint);
          extra_amount:=extra.total_minor;
          extra_lines:=extra.service_lines;
        END IF;
        -- Прямой SQL не может подменить сумму, версии и согласие в пакете.
        IF NEW.snapshot->>'estimateRevisionId' IS DISTINCT FROM NEW.revision_id::text
          OR NEW.snapshot->>'estimateVersion' IS DISTINCT FROM base_version::text
          OR NEW.snapshot->>'estimateDigest' IS DISTINCT FROM btrim(base_digest)
          OR NEW.snapshot->'lines' IS DISTINCT FROM base_lines
          OR NEW.snapshot->'extraLines' IS DISTINCT FROM extra_lines
          OR NEW.snapshot->>'approvedBaseMinor' IS DISTINCT FROM base_total::text
          OR NEW.snapshot->>'approvedExtraMinor' IS DISTINCT FROM extra_amount::text
          OR NEW.snapshot->>'approvedTotalMinor' IS DISTINCT FROM full_total::text
          OR NEW.snapshot->>'extraRevisionId' IS DISTINCT FROM extra_id::text
          OR NEW.snapshot->>'extraVersion' IS DISTINCT FROM extra_version::text
          OR NEW.snapshot->>'extraDigest' IS DISTINCT FROM extra_digest
          OR NEW.snapshot->>'simulatedDueMinor' IS DISTINCT FROM '0'
          OR NEW.snapshot->>'releaseAllowed' IS DISTINCT FROM 'false'
          OR NEW.snapshot->>'legalGatePassed' IS DISTINCT FROM 'false'
        THEN RAISE EXCEPTION 'preview snapshot does not match approved amounts'; END IF;
        SELECT COALESCE(sum(amount_minor) FILTER(WHERE kind='SIM_PAYMENT'),0),
               COALESCE(sum(amount_minor) FILTER(WHERE kind='SIM_REFUND'),0)
        INTO paid,refunded
        FROM auto_order_demo_cash_event WHERE work_order_id=NEW.work_order_id;
        IF paid-refunded<>full_total
          OR NEW.snapshot->>'simulatedPaidMinor' IS DISTINCT FROM paid::text
          OR NEW.snapshot->>'simulatedRefundedMinor' IS DISTINCT FROM refunded::text
        THEN RAISE EXCEPTION 'demo settlement not reconciled'; END IF;
        IF EXISTS(
          SELECT 1 FROM auto_stock_lot_work_order_issue i
          WHERE i.work_order_id=NEW.work_order_id
            AND NOT EXISTS(SELECT 1 FROM auto_stock_mechanic_handoff h
                           WHERE h.issue_id=i.id)
        ) OR EXISTS(
          SELECT 1 FROM auto_work_order_stock_event e
          WHERE e.work_order_id=NEW.work_order_id
          GROUP BY e.sku_id
          HAVING sum(CASE WHEN action='RESERVE' THEN quantity
                          WHEN action IN ('ISSUE','RELEASE') THEN -quantity
                          ELSE 0 END)<>0
        ) THEN RAISE EXCEPTION 'stock reservations or handoffs remain'; END IF;
        RETURN NEW;
      END $f$
    """)


def downgrade():
    # Восстанавливаем исходные DDL из текущей базы, не удаляя историю актов.
    op.execute("""
      DO $f$
      DECLARE item record;
      BEGIN
        FOR item IN SELECT definition FROM auto_demo_guard_restore
                    ORDER BY name LOOP
          EXECUTE item.definition;
        END LOOP;
      END $f$
    """)
    op.execute("DROP FUNCTION auto_demo_billable_total(uuid,uuid)")
    op.execute("DROP TABLE auto_demo_guard_restore")
