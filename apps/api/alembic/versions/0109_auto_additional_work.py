"""Independent synthetic supplementary-work consent after the initial approved estimate.

Revision ID: 0109_auto_additional_work
Revises: 0108_auto_work_order_assignment
"""
from alembic import op

revision="0109_auto_additional_work"
down_revision="0108_auto_work_order_assignment"
branch_labels=None
depends_on=None


def upgrade():
    op.execute("""
      CREATE TABLE auto_work_order_extra_revision (
        id uuid PRIMARY KEY,
        work_order_id uuid NOT NULL REFERENCES auto_work_order(id),
        organization_id uuid NOT NULL REFERENCES auto_organization(id),
        location_id uuid NOT NULL REFERENCES auto_service_location(id),
        base_revision_id uuid NOT NULL REFERENCES auto_work_order_estimate_revision(id),
        version integer NOT NULL CHECK(version BETWEEN 1 AND 10),
        actor_id uuid NOT NULL REFERENCES app_user(id),
        request_key uuid NOT NULL,
        fingerprint char(64) NOT NULL,
        total_minor bigint NOT NULL CHECK(total_minor BETWEEN 0 AND 1000000000000),
        service_lines jsonb NOT NULL CHECK(jsonb_typeof(service_lines)='array'
          AND jsonb_array_length(service_lines) BETWEEN 1 AND 20),
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        UNIQUE(work_order_id,version),
        UNIQUE(work_order_id,request_key)
      )
    """)
    op.execute("""
      CREATE TABLE auto_work_order_extra_decision (
        id uuid PRIMARY KEY,
        revision_id uuid NOT NULL UNIQUE REFERENCES auto_work_order_extra_revision(id),
        work_order_id uuid NOT NULL REFERENCES auto_work_order(id),
        actor_id uuid NOT NULL REFERENCES app_user(id),
        request_key uuid NOT NULL,
        decision text NOT NULL CHECK(decision IN ('APPROVED','DECLINED')),
        fingerprint char(64) NOT NULL,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        UNIQUE(actor_id,request_key)
      )
    """)
    op.execute("""
      CREATE FUNCTION guard_auto_extra_revision()
      RETURNS trigger LANGUAGE plpgsql AS $f$
      DECLARE
        line jsonb;
        svc record;
        duration integer;
        rate bigint;
        quantity integer;
        coefficient integer;
        discount integer;
        amount bigint;
        tally bigint:=0;
      BEGIN
        IF NOT EXISTS (
          SELECT 1 FROM auto_work_order w
          JOIN auto_work_order_estimate_revision base
            ON base.id=NEW.base_revision_id AND base.work_order_id=w.id
          JOIN auto_work_order_estimate_decision consent
            ON consent.revision_id=base.id AND consent.decision='APPROVED'
          JOIN auto_organization o ON o.id=w.organization_id AND o.disabled_at IS NULL
          JOIN auto_service_location loc ON loc.id=w.location_id
            AND loc.organization_id=o.id AND loc.disabled_at IS NULL
          JOIN auto_appointment a ON a.id=w.appointment_id
          JOIN LATERAL (
            SELECT COALESCE((SELECT rs.new_booking_id
              FROM auto_appointment_reschedule rs WHERE rs.appointment_id=a.id
              ORDER BY rs.created_at DESC,rs.id DESC LIMIT 1),a.booking_id) AS booking_id
          ) latest_booking ON TRUE
          JOIN auto_bay_booking b ON b.id=latest_booking.booking_id
            AND b.organization_id=w.organization_id
            AND b.location_id=w.location_id AND b.canceled_at IS NULL
          JOIN auto_vehicle_customer_link proof
            ON proof.id=a.customer_link_id AND proof.vehicle_id=a.vehicle_id
            AND proof.verified_at IS NOT NULL AND proof.revoked_at IS NULL
          WHERE w.id=NEW.work_order_id AND w.organization_id=NEW.organization_id
            AND w.location_id=NEW.location_id
            AND a.organization_id=w.organization_id AND a.location_id=w.location_id
            AND NOT EXISTS (SELECT 1 FROM auto_work_order_execution_finish f
              JOIN auto_work_order_execution e ON e.id=f.execution_id
              WHERE e.work_order_id=w.id)
            AND NOT EXISTS (SELECT 1 FROM auto_work_order_estimate_revision newer
              WHERE newer.work_order_id=w.id AND newer.version>base.version)
          FOR SHARE OF w,base,consent,o,loc,a,b,proof
        ) THEN RAISE EXCEPTION 'additional work requires active approved estimate'; END IF;
        IF NEW.version<>1+COALESCE((SELECT max(version)
          FROM auto_work_order_extra_revision WHERE work_order_id=NEW.work_order_id),0)
          OR EXISTS (
            SELECT 1 FROM auto_work_order_extra_decision d
            JOIN auto_work_order_extra_revision r ON r.id=d.revision_id
            WHERE r.work_order_id=NEW.work_order_id AND d.decision='APPROVED'
          )
        THEN RAISE EXCEPTION 'additional work version finalized'; END IF;
        IF (SELECT count(DISTINCT x.value->>'serviceId')
            FROM jsonb_array_elements(NEW.service_lines) x)
           <>jsonb_array_length(NEW.service_lines)
        THEN RAISE EXCEPTION 'duplicate additional work service'; END IF;
        FOR line IN SELECT value FROM jsonb_array_elements(NEW.service_lines)
        LOOP
          IF (SELECT count(*) FROM jsonb_object_keys(line))<>11
            OR line->>'pricingMethod' IS DISTINCT FROM 'NORM_HOUR_FACTORED'
            OR COALESCE(line->>'qualificationCode','')
                 NOT IN ('GENERAL','DIAGNOSTIC','MASTER')
            OR COALESCE(line->>'rateMinorPerHour','') !~ '^[0-9]{1,11}$'
            OR COALESCE(line->>'quantity','') !~ '^[0-9]{1,2}$'
            OR COALESCE(line->>'coefficientBps','') !~ '^[0-9]{5}$'
            OR COALESCE(line->>'discountBps','') !~ '^[0-9]{1,4}$'
            OR COALESCE(line->>'amountMinor','') !~ '^[0-9]{1,13}$'
            OR jsonb_typeof(line->'rateMinorPerHour')<>'number'
            OR jsonb_typeof(line->'quantity')<>'number'
            OR jsonb_typeof(line->'coefficientBps')<>'number'
            OR jsonb_typeof(line->'discountBps')<>'number'
            OR jsonb_typeof(line->'amountMinor')<>'number'
          THEN RAISE EXCEPTION 'invalid additional work line'; END IF;
          rate:=(line->>'rateMinorPerHour')::bigint;
          quantity:=(line->>'quantity')::integer;
          coefficient:=(line->>'coefficientBps')::integer;
          discount:=(line->>'discountBps')::integer;
          IF rate>10000000000 OR quantity NOT BETWEEN 1 AND 20
            OR coefficient NOT BETWEEN 10000 AND 30000
            OR discount NOT BETWEEN 0 AND 2500
          THEN RAISE EXCEPTION 'additional work pricing limits'; END IF;
          SELECT s.id,s.code,s.title,s.duration_minutes INTO svc
          FROM auto_service_catalog s
          WHERE s.id::text=line->>'serviceId'
            AND s.organization_id=NEW.organization_id
            AND s.location_id=NEW.location_id
            AND NOT EXISTS (SELECT 1 FROM auto_work_order_service chosen
              WHERE chosen.work_order_id=NEW.work_order_id
                AND chosen.service_id=s.id)
            FOR SHARE OF s;
          IF NOT FOUND OR line->>'code' IS DISTINCT FROM svc.code
            OR line->>'title' IS DISTINCT FROM svc.title
            OR line->>'durationMinutes' IS DISTINCT FROM svc.duration_minutes::text
            OR jsonb_typeof(line->'durationMinutes')<>'number'
          THEN RAISE EXCEPTION 'additional service outside catalog or in base estimate'; END IF;
          amount:=floor((
            svc.duration_minutes::numeric*rate*quantity*coefficient*(10000-discount)
              +3000000000::numeric)/6000000000::numeric)::bigint;
          IF amount<>(line->>'amountMinor')::bigint
          THEN RAISE EXCEPTION 'additional work amount mismatch'; END IF;
          tally:=tally+amount;
        END LOOP;
        IF tally<>NEW.total_minor THEN
          RAISE EXCEPTION 'additional work total mismatch'; END IF;
        RETURN NEW;
      END $f$
    """)
    op.execute("""CREATE TRIGGER auto_extra_revision_guard
      BEFORE INSERT ON auto_work_order_extra_revision FOR EACH ROW
      EXECUTE FUNCTION guard_auto_extra_revision()""")
    op.execute("""
      CREATE FUNCTION guard_auto_extra_decision()
      RETURNS trigger LANGUAGE plpgsql AS $f$
      BEGIN
        IF NOT EXISTS (
          SELECT 1 FROM auto_work_order_extra_revision r
          JOIN auto_work_order w ON w.id=r.work_order_id
          JOIN auto_appointment a ON a.id=w.appointment_id
          JOIN auto_vehicle_customer_link proof
            ON proof.id=a.customer_link_id AND proof.vehicle_id=a.vehicle_id
          JOIN account_customer_link acl
            ON acl.customer_id=proof.customer_id
            AND acl.user_id=NEW.actor_id AND acl.revoked_at IS NULL
          JOIN user_role role ON role.user_id=NEW.actor_id AND role.role_code='CLIENT'
            AND role.revoked_at IS NULL
            AND (role.expires_at IS NULL OR role.expires_at>now())
          WHERE r.id=NEW.revision_id AND r.work_order_id=NEW.work_order_id
            AND proof.verified_at IS NOT NULL AND proof.revoked_at IS NULL
            AND NOT EXISTS (SELECT 1 FROM auto_work_order_extra_revision newer
              WHERE newer.work_order_id=r.work_order_id AND newer.version>r.version)
            AND NOT EXISTS (SELECT 1 FROM auto_work_order_execution_finish f
              JOIN auto_work_order_execution e ON e.id=f.execution_id
              WHERE e.work_order_id=r.work_order_id)
            AND NOT EXISTS (SELECT 1 FROM auto_work_order_extra_decision d
              JOIN auto_work_order_extra_revision accepted ON accepted.id=d.revision_id
              WHERE accepted.work_order_id=r.work_order_id AND d.decision='APPROVED')
          FOR SHARE OF r,w,a,proof,acl,role
        ) THEN RAISE EXCEPTION 'additional consent scope unavailable'; END IF;
        RETURN NEW;
      END $f$
    """)
    op.execute("""CREATE TRIGGER auto_extra_decision_guard
      BEFORE INSERT ON auto_work_order_extra_decision FOR EACH ROW
      EXECUTE FUNCTION guard_auto_extra_decision()""")
    # Only an explicitly accepted additional revision can authorize labor.
    op.execute("""
      CREATE OR REPLACE FUNCTION guard_auto_execution_labor()
      RETURNS trigger LANGUAGE plpgsql AS $f$
      BEGIN
        IF NOT EXISTS(
          SELECT 1 FROM auto_work_order_execution e
          JOIN auto_work_order_estimate_revision r ON r.id=e.revision_id
          WHERE e.id=NEW.execution_id
            AND (r.service_lines @> jsonb_build_array(jsonb_build_object(
              'serviceId',NEW.service_id::text))
              OR EXISTS(
                SELECT 1 FROM auto_work_order_extra_revision extra
                JOIN auto_work_order_extra_decision d ON d.revision_id=extra.id
                  AND d.decision='APPROVED'
                WHERE extra.work_order_id=e.work_order_id
                  AND extra.service_lines @> jsonb_build_array(jsonb_build_object(
                    'serviceId',NEW.service_id::text))
              ))
            AND NOT EXISTS(SELECT 1 FROM auto_work_order_execution_finish f
              WHERE f.execution_id=e.id)
          FOR SHARE OF e,r
        ) THEN RAISE EXCEPTION 'labor service not individually approved'; END IF;
        RETURN NEW;
      END $f$
    """)
    op.execute("""
      CREATE OR REPLACE FUNCTION guard_auto_execution_finish()
      RETURNS trigger LANGUAGE plpgsql AS $f$
      BEGIN
        IF NOT EXISTS(
          SELECT 1 FROM auto_work_order_execution e
          JOIN auto_work_order_estimate_revision r ON r.id=e.revision_id
          WHERE e.id=NEW.execution_id
            AND NOT EXISTS (
              SELECT 1 FROM auto_work_order_extra_revision extra
              LEFT JOIN auto_work_order_extra_decision d ON d.revision_id=extra.id
              WHERE extra.work_order_id=e.work_order_id AND d.id IS NULL
            )
            AND (SELECT count(*) FROM auto_work_order_labor_entry labor
                WHERE labor.execution_id=e.id)=
                jsonb_array_length(r.service_lines)+COALESCE((
                  SELECT jsonb_array_length(extra.service_lines)
                  FROM auto_work_order_extra_revision extra
                  JOIN auto_work_order_extra_decision d ON d.revision_id=extra.id
                    AND d.decision='APPROVED'
                  WHERE extra.work_order_id=e.work_order_id),0)
            AND NOT EXISTS (
              SELECT 1 FROM auto_stock_lot_work_order_issue i
              WHERE i.work_order_id=e.work_order_id
                AND NOT EXISTS (SELECT 1 FROM auto_stock_mechanic_handoff h
                  WHERE h.issue_id=i.id)
            )
          FOR SHARE OF e,r
        ) THEN RAISE EXCEPTION 'additional consent, labor or handoff incomplete'; END IF;
        RETURN NEW;
      END $f$
    """)
    for table in ("auto_work_order_extra_revision","auto_work_order_extra_decision"):
        op.execute(f"""CREATE TRIGGER {table}_immutable
          BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW
          EXECUTE FUNCTION immutable_auto_stock_event()""")
        op.execute(f"REVOKE ALL ON {table} FROM PUBLIC")


def downgrade():
    # Synthetic disposable DB only. Never erase real consent evidence.
    op.execute("DROP TABLE auto_work_order_extra_decision")
    op.execute("DROP TABLE auto_work_order_extra_revision")
    op.execute("DROP FUNCTION guard_auto_extra_decision()")
    op.execute("DROP FUNCTION guard_auto_extra_revision()")
    op.execute("""
      CREATE OR REPLACE FUNCTION guard_auto_execution_labor()
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
      CREATE OR REPLACE FUNCTION guard_auto_execution_finish()
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
