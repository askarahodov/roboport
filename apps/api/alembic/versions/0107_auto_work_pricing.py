"""Versioned synthetic quote: work quantity, coefficient and bounded discount.

Revision ID: 0107_auto_work_pricing
Revises: 0105_auto_norm_hour_quote
The old norm-hour SQL guard remains defined for downgrade compatibility.
"""
from alembic import op

revision="0107_auto_work_pricing"
down_revision="0106_auto_intake_photo_act"
branch_labels=None
depends_on=None


def upgrade():
    # Replace only the INSERT trigger. Prior 0105 guard remains available and
    # is restored when rolling back on a disposable synthetic database.
    op.execute("""DROP TRIGGER auto_norm_hour_quote_guard
      ON auto_work_order_estimate_revision""")
    op.execute("""CREATE FUNCTION guard_auto_work_pricing_quote()
    RETURNS trigger LANGUAGE plpgsql AS $f$
    DECLARE
      line jsonb;
      duration integer;
      service_code text;
      service_title text;
      rate bigint;
      quantity integer;
      coeff integer;
      discount integer;
      expected_minor bigint;
      total_minor bigint := 0;
      selected_count bigint;
      line_count integer;
      factor_mode boolean;
    BEGIN
      IF NOT EXISTS (
        SELECT 1 FROM jsonb_array_elements(NEW.service_lines) AS elements(value)
        WHERE elements.value ? 'pricingMethod' OR elements.value ? 'rateMinorPerHour'
          OR elements.value ? 'qualificationCode' OR elements.value ? 'quantity'
          OR elements.value ? 'coefficientBps' OR elements.value ? 'discountBps'
      ) THEN RETURN NEW; END IF;

      line_count:=jsonb_array_length(NEW.service_lines);
      SELECT count(*) INTO selected_count FROM auto_work_order_service
        WHERE work_order_id=NEW.work_order_id
          AND organization_id=NEW.organization_id
          AND location_id=NEW.location_id;
      IF line_count<>selected_count OR
        (SELECT count(DISTINCT elements.value->>'serviceId')
         FROM jsonb_array_elements(NEW.service_lines) AS elements(value))<>line_count
      THEN RAISE EXCEPTION 'quoted service set does not match selected services'; END IF;

      FOR line IN SELECT value FROM jsonb_array_elements(NEW.service_lines)
      LOOP
        factor_mode:=COALESCE(line->>'pricingMethod'='NORM_HOUR_FACTORED',FALSE);
        IF COALESCE(line->>'pricingMethod','') NOT IN ('NORM_HOUR','NORM_HOUR_FACTORED')
          OR COALESCE(line->>'qualificationCode','')
             NOT IN ('GENERAL','DIAGNOSTIC','MASTER')
          OR jsonb_typeof(line->'rateMinorPerHour') IS DISTINCT FROM 'number'
          OR COALESCE(line->>'rateMinorPerHour','') !~ '^[0-9]{1,11}$'
          OR jsonb_typeof(line->'amountMinor') IS DISTINCT FROM 'number'
          OR COALESCE(line->>'amountMinor','') !~ '^[0-9]{1,13}$'
          OR jsonb_typeof(line->'durationMinutes') IS DISTINCT FROM 'number'
          OR COALESCE(line->>'durationMinutes','') !~ '^[0-9]{1,4}$'
          OR (SELECT count(*) FROM jsonb_object_keys(line))<>
               (CASE WHEN factor_mode THEN 11 ELSE 8 END)
        THEN RAISE EXCEPTION 'invalid priced line'; END IF;

        rate:=(line->>'rateMinorPerHour')::bigint;
        IF rate>10000000000 THEN RAISE EXCEPTION 'tariff rate limit exceeded'; END IF;

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
        THEN RAISE EXCEPTION 'priced service snapshot mismatch'; END IF;

        IF factor_mode THEN
          IF jsonb_typeof(line->'quantity') IS DISTINCT FROM 'number'
            OR COALESCE(line->>'quantity','') !~ '^[0-9]{1,2}$'
            OR jsonb_typeof(line->'coefficientBps') IS DISTINCT FROM 'number'
            OR COALESCE(line->>'coefficientBps','') !~ '^[0-9]{5}$'
            OR jsonb_typeof(line->'discountBps') IS DISTINCT FROM 'number'
            OR COALESCE(line->>'discountBps','') !~ '^[0-9]{1,4}$'
          THEN RAISE EXCEPTION 'invalid quantity, coefficient or discount'; END IF;
          quantity:=(line->>'quantity')::integer;
          coeff:=(line->>'coefficientBps')::integer;
          discount:=(line->>'discountBps')::integer;
          IF quantity NOT BETWEEN 1 AND 20 OR
             coeff NOT BETWEEN 10000 AND 30000 OR
             discount NOT BETWEEN 0 AND 2500
          THEN RAISE EXCEPTION 'work-pricing policy limit exceeded'; END IF;
          expected_minor:=floor((
              duration::numeric*rate*quantity*coeff*(10000-discount)
              +3000000000::numeric
            ) / 6000000000::numeric)::bigint;
        ELSE
          expected_minor:=(duration::bigint*rate+30)/60;
        END IF;
        IF (line->>'amountMinor')::bigint<>expected_minor
        THEN RAISE EXCEPTION 'priced line amount mismatch'; END IF;
        total_minor:=total_minor+expected_minor;
      END LOOP;
      IF NEW.total_minor<>total_minor
      THEN RAISE EXCEPTION 'priced quote total mismatch'; END IF;
      RETURN NEW;
    END; $f$""")
    op.execute("""CREATE TRIGGER auto_work_pricing_quote_guard
      BEFORE INSERT ON auto_work_order_estimate_revision
      FOR EACH ROW EXECUTE FUNCTION guard_auto_work_pricing_quote()""")


def downgrade():
    # Synthetic disposable DB only. A real signed quote must never be rolled
    # back by removing evidence or weakening checks in a production database.
    op.execute("""DROP TRIGGER auto_work_pricing_quote_guard
      ON auto_work_order_estimate_revision""")
    op.execute("DROP FUNCTION guard_auto_work_pricing_quote()")
    op.execute("""CREATE TRIGGER auto_norm_hour_quote_guard
      BEFORE INSERT ON auto_work_order_estimate_revision
      FOR EACH ROW EXECUTE FUNCTION guard_auto_norm_hour_quote()""")
