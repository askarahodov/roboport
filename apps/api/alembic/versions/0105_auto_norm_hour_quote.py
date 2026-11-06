"""DB-validated norm-hour estimate line arithmetic; no editable tariff history.

Revision ID: 0105_auto_norm_hour_quote
Revises: 0104_auto_diagnostic_evidence
Synthetic-only tariff proposal, not a payroll grade or fiscal price.
"""
from alembic import op

revision="0105_auto_norm_hour_quote"
down_revision="0104_auto_diagnostic_evidence"
branch_labels=None
depends_on=None


def upgrade():
    op.execute("""CREATE FUNCTION guard_auto_norm_hour_quote()
    RETURNS trigger LANGUAGE plpgsql AS $f$
    DECLARE
      line jsonb;
      duration integer;
      service_code text;
      service_title text;
      rate bigint;
      expected_minor bigint;
      total_minor bigint := 0;
      selected_count bigint;
      line_count integer;
    BEGIN
      -- All legacy flat-price revisions retain their original contract.
      IF NOT EXISTS (
        SELECT 1 FROM jsonb_array_elements(NEW.service_lines) AS elements(value)
        WHERE elements.value ? 'pricingMethod' OR elements.value ? 'rateMinorPerHour'
          OR elements.value ? 'qualificationCode'
      ) THEN RETURN NEW; END IF;

      line_count:=jsonb_array_length(NEW.service_lines);
      SELECT count(*) INTO selected_count FROM auto_work_order_service
      WHERE work_order_id=NEW.work_order_id
        AND organization_id=NEW.organization_id
        AND location_id=NEW.location_id;
      IF line_count<>selected_count OR
        (SELECT count(DISTINCT elements.value->>'serviceId')
         FROM jsonb_array_elements(NEW.service_lines) AS elements(value))<>line_count
      THEN RAISE EXCEPTION 'norm-hour quote must include every selected service once'; END IF;

      FOR line IN SELECT value FROM jsonb_array_elements(NEW.service_lines)
      LOOP
        IF line->>'pricingMethod' IS DISTINCT FROM 'NORM_HOUR'
          OR COALESCE(line->>'qualificationCode','') NOT IN ('GENERAL','DIAGNOSTIC','MASTER')
          OR jsonb_typeof(line->'rateMinorPerHour') IS DISTINCT FROM 'number'
          OR COALESCE(line->>'rateMinorPerHour','') !~ '^[0-9]{1,11}$'
          OR jsonb_typeof(line->'amountMinor') IS DISTINCT FROM 'number'
          OR COALESCE(line->>'amountMinor','') !~ '^[0-9]{1,13}$'
          OR jsonb_typeof(line->'durationMinutes') IS DISTINCT FROM 'number'
          OR COALESCE(line->>'durationMinutes','') !~ '^[0-9]{1,4}$'
          OR (SELECT count(*) FROM jsonb_object_keys(line))<>8
        THEN RAISE EXCEPTION 'invalid norm-hour line'; END IF;

        rate:=(line->>'rateMinorPerHour')::bigint;
        IF rate>10000000000 THEN RAISE EXCEPTION 'norm-hour tariff limit'; END IF;

        SELECT s.duration_minutes,s.code,s.title
          INTO duration,service_code,service_title
        FROM auto_work_order_service chosen
        JOIN auto_service_catalog s ON s.id=chosen.service_id
          AND s.organization_id=chosen.organization_id
          AND s.location_id=chosen.location_id
        WHERE chosen.work_order_id=NEW.work_order_id
          AND chosen.organization_id=NEW.organization_id
          AND chosen.location_id=NEW.location_id
          AND s.id::text=line->>'serviceId';
        IF duration IS NULL OR line->>'code' IS DISTINCT FROM service_code
          OR line->>'title' IS DISTINCT FROM service_title
          OR (line->>'durationMinutes')::integer<>duration
        THEN RAISE EXCEPTION 'norm-hour service snapshot mismatch'; END IF;

        expected_minor:=(duration::bigint*rate+30)/60;
        IF (line->>'amountMinor')::bigint<>expected_minor
        THEN RAISE EXCEPTION 'norm-hour amount mismatch'; END IF;
        total_minor:=total_minor+expected_minor;
      END LOOP;
      IF NEW.total_minor<>total_minor
      THEN RAISE EXCEPTION 'norm-hour total mismatch'; END IF;
      RETURN NEW;
    END $f$""")
    op.execute("""CREATE TRIGGER auto_norm_hour_quote_guard
      BEFORE INSERT ON auto_work_order_estimate_revision
      FOR EACH ROW EXECUTE FUNCTION guard_auto_norm_hour_quote()""")


def downgrade():
    # Only disposable synthetic DB, not a permission to erase customer quotes.
    op.execute("DROP TRIGGER auto_norm_hour_quote_guard ON auto_work_order_estimate_revision")
    op.execute("DROP FUNCTION guard_auto_norm_hour_quote()")
