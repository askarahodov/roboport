"""Branch-approved, immutable work tariffs with independent manager approval.

Revision ID: 0110_auto_branch_work_tariffs
Revises: 0109_auto_additional_work
"""
from alembic import op

revision="0110_auto_branch_work_tariffs"
down_revision="0109_auto_additional_work"
branch_labels=None
depends_on=None


def upgrade():
    op.execute("""
      CREATE TABLE auto_work_tariff_revision (
        id uuid PRIMARY KEY,
        organization_id uuid NOT NULL REFERENCES auto_organization(id),
        location_id uuid NOT NULL REFERENCES auto_service_location(id),
        qualification_code text NOT NULL
          CHECK (qualification_code IN ('GENERAL','DIAGNOSTIC','MASTER')),
        version integer NOT NULL CHECK (version BETWEEN 1 AND 30),
        rate_minor_per_hour bigint NOT NULL CHECK(rate_minor_per_hour BETWEEN 0 AND 10000000000),
        max_discount_bps integer NOT NULL CHECK(max_discount_bps BETWEEN 0 AND 2500),
        actor_id uuid NOT NULL REFERENCES app_user(id),
        request_key uuid NOT NULL UNIQUE,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        UNIQUE(location_id,qualification_code,version)
      )
    """)
    op.execute("""
      CREATE TABLE auto_work_tariff_approval (
        id uuid PRIMARY KEY,
        revision_id uuid NOT NULL UNIQUE REFERENCES auto_work_tariff_revision(id),
        approved_by uuid NOT NULL REFERENCES app_user(id),
        request_key uuid NOT NULL UNIQUE,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp()
      )
    """)
    op.execute("""
      CREATE FUNCTION guard_auto_work_tariff_revision()
      RETURNS trigger LANGUAGE plpgsql AS $f$
      BEGIN
        IF NOT EXISTS(
          SELECT 1 FROM auto_service_location loc
          JOIN auto_organization o ON o.id=loc.organization_id AND o.disabled_at IS NULL
          JOIN auto_organization_role_grant role_grant
            ON role_grant.organization_id=o.id AND role_grant.user_id=NEW.actor_id
            AND role_grant.role_code='MANAGER' AND role_grant.revoked_at IS NULL
            AND (role_grant.location_id IS NULL OR role_grant.location_id=loc.id)
          JOIN app_user actor ON actor.id=NEW.actor_id AND actor.status='ACTIVE'
          WHERE loc.id=NEW.location_id AND loc.organization_id=NEW.organization_id
            AND loc.disabled_at IS NULL
          FOR SHARE OF loc,o,role_grant,actor
        ) THEN RAISE EXCEPTION 'tariff proposer has no branch authority'; END IF;
        IF NEW.version<>1+COALESCE((
          SELECT max(version) FROM auto_work_tariff_revision
          WHERE location_id=NEW.location_id
            AND qualification_code=NEW.qualification_code),0)
        THEN RAISE EXCEPTION 'tariff version conflict'; END IF;
        IF EXISTS(
          SELECT 1 FROM auto_work_tariff_revision p
          LEFT JOIN auto_work_tariff_approval a ON a.revision_id=p.id
          WHERE p.location_id=NEW.location_id
            AND p.qualification_code=NEW.qualification_code AND a.id IS NULL
        ) THEN RAISE EXCEPTION 'unapproved tariff proposal exists'; END IF;
        RETURN NEW;
      END $f$
    """)
    op.execute("""CREATE TRIGGER auto_work_tariff_revision_guard
      BEFORE INSERT ON auto_work_tariff_revision FOR EACH ROW
      EXECUTE FUNCTION guard_auto_work_tariff_revision()""")
    op.execute("""
      CREATE FUNCTION guard_auto_work_tariff_approval()
      RETURNS trigger LANGUAGE plpgsql AS $f$
      DECLARE proposal auto_work_tariff_revision%ROWTYPE;
      BEGIN
        SELECT * INTO proposal FROM auto_work_tariff_revision
        WHERE id=NEW.revision_id FOR SHARE;
        IF NOT FOUND OR NEW.approved_by=proposal.actor_id
          OR EXISTS (
            SELECT 1 FROM auto_work_tariff_revision newer
            WHERE newer.location_id=proposal.location_id
              AND newer.qualification_code=proposal.qualification_code
              AND newer.version>proposal.version
          )
          OR NOT EXISTS (
            SELECT 1 FROM auto_service_location loc
            JOIN auto_organization o ON o.id=loc.organization_id AND o.disabled_at IS NULL
            JOIN auto_organization_role_grant role_grant
              ON role_grant.organization_id=o.id AND role_grant.user_id=NEW.approved_by
              AND role_grant.role_code='MANAGER' AND role_grant.revoked_at IS NULL
              AND (role_grant.location_id IS NULL OR role_grant.location_id=loc.id)
            JOIN app_user actor ON actor.id=NEW.approved_by AND actor.status='ACTIVE'
            WHERE loc.id=proposal.location_id
              AND loc.organization_id=proposal.organization_id
              AND loc.disabled_at IS NULL
            FOR SHARE OF loc,o,role_grant,actor
          )
        THEN RAISE EXCEPTION 'tariff approval requires distinct authorized manager'; END IF;
        RETURN NEW;
      END $f$
    """)
    op.execute("""CREATE TRIGGER auto_work_tariff_approval_guard
      BEFORE INSERT ON auto_work_tariff_approval FOR EACH ROW
      EXECUTE FUNCTION guard_auto_work_tariff_approval()""")
    op.execute("""
      ALTER TABLE auto_work_order_estimate_revision
        ADD COLUMN tariff_snapshot jsonb NOT NULL DEFAULT '[]'::jsonb
        CHECK(jsonb_typeof(tariff_snapshot)='array'
          AND jsonb_array_length(tariff_snapshot) BETWEEN 0 AND 50)
    """)
    op.execute("""
      ALTER TABLE auto_work_order_extra_revision
        ADD COLUMN tariff_snapshot jsonb NOT NULL DEFAULT '[]'::jsonb
        CHECK(jsonb_typeof(tariff_snapshot)='array'
          AND jsonb_array_length(tariff_snapshot) BETWEEN 0 AND 20)
    """)
    op.execute("""
      CREATE FUNCTION guard_auto_branch_tariff_snapshot()
      RETURNS trigger LANGUAGE plpgsql AS $f$
      DECLARE
        line jsonb;
        matched jsonb;
        pricing record;
        approved_count integer;
      BEGIN
        -- Location SHARE lock serializes publishing with tariff approval,
        -- which takes the same branch row FOR UPDATE.
        PERFORM 1 FROM auto_service_location loc
          JOIN auto_organization o ON o.id=loc.organization_id
          WHERE loc.id=NEW.location_id AND loc.organization_id=NEW.organization_id
            AND loc.disabled_at IS NULL AND o.disabled_at IS NULL
          FOR SHARE OF loc,o;
        IF NOT FOUND THEN RAISE EXCEPTION 'inactive tariff branch'; END IF;

        SELECT count(*) INTO approved_count
          FROM auto_work_tariff_revision p
          JOIN auto_work_tariff_approval a ON a.revision_id=p.id
          WHERE p.organization_id=NEW.organization_id AND p.location_id=NEW.location_id;

        IF approved_count=0 THEN
          IF NEW.tariff_snapshot<>'[]'::jsonb
          THEN RAISE EXCEPTION 'unapproved tariff snapshot'; END IF;
          RETURN NEW;
        END IF;
        IF jsonb_array_length(NEW.service_lines)<>jsonb_array_length(NEW.tariff_snapshot)
           OR (SELECT count(DISTINCT item.value->>'serviceId')
                FROM jsonb_array_elements(NEW.tariff_snapshot) item)
              <>jsonb_array_length(NEW.tariff_snapshot)
        THEN RAISE EXCEPTION 'tariff snapshot service-set mismatch'; END IF;
        FOR line IN SELECT value FROM jsonb_array_elements(NEW.service_lines)
        LOOP
          IF line->>'pricingMethod' IS DISTINCT FROM 'NORM_HOUR_FACTORED'
          THEN RAISE EXCEPTION 'branch tariff requires factored work pricing'; END IF;
          SELECT p.id,p.version,p.qualification_code,p.rate_minor_per_hour,
                 p.max_discount_bps INTO pricing
          FROM auto_work_tariff_revision p
          JOIN auto_work_tariff_approval a ON a.revision_id=p.id
          WHERE p.organization_id=NEW.organization_id AND p.location_id=NEW.location_id
            AND p.qualification_code=line->>'qualificationCode'
          ORDER BY p.version DESC LIMIT 1 FOR SHARE OF p,a;
          IF NOT FOUND OR line->>'rateMinorPerHour' IS DISTINCT FROM
              pricing.rate_minor_per_hour::text
            OR COALESCE(line->>'discountBps','') !~ '^[0-9]{1,4}$'
            OR (line->>'discountBps')::integer>pricing.max_discount_bps
          THEN RAISE EXCEPTION 'rate or discount violates approved branch tariff'; END IF;
          matched:=jsonb_build_object(
            'serviceId',line->>'serviceId','policyId',pricing.id::text,
            'version',pricing.version,'qualificationCode',pricing.qualification_code,
            'rateMinorPerHour',pricing.rate_minor_per_hour,
            'maxDiscountBps',pricing.max_discount_bps);
          IF NOT NEW.tariff_snapshot @> jsonb_build_array(matched)
            OR (SELECT count(*) FROM jsonb_array_elements(NEW.tariff_snapshot) entry
                WHERE entry.value->>'serviceId'=line->>'serviceId'
                  AND entry.value=matched)<>1
          THEN RAISE EXCEPTION 'tariff snapshot differs from approved policy'; END IF;
        END LOOP;
        RETURN NEW;
      END $f$
    """)
    op.execute("""CREATE TRIGGER auto_branch_tariff_guard_estimate
      BEFORE INSERT ON auto_work_order_estimate_revision FOR EACH ROW
      EXECUTE FUNCTION guard_auto_branch_tariff_snapshot()""")
    op.execute("""CREATE TRIGGER auto_branch_tariff_guard_extra
      BEFORE INSERT ON auto_work_order_extra_revision FOR EACH ROW
      EXECUTE FUNCTION guard_auto_branch_tariff_snapshot()""")
    for table in ("auto_work_tariff_revision","auto_work_tariff_approval"):
        op.execute(f"""CREATE TRIGGER {table}_immutable
          BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW
          EXECUTE FUNCTION immutable_auto_stock_event()""")
        op.execute(f"REVOKE ALL ON {table} FROM PUBLIC")


def downgrade():
    op.execute("DROP TRIGGER auto_branch_tariff_guard_extra ON auto_work_order_extra_revision")
    op.execute("DROP TRIGGER auto_branch_tariff_guard_estimate ON auto_work_order_estimate_revision")
    op.execute("DROP FUNCTION guard_auto_branch_tariff_snapshot()")
    op.execute("ALTER TABLE auto_work_order_extra_revision DROP COLUMN tariff_snapshot")
    op.execute("ALTER TABLE auto_work_order_estimate_revision DROP COLUMN tariff_snapshot")
    op.execute("DROP TABLE auto_work_tariff_approval")
    op.execute("DROP TABLE auto_work_tariff_revision")
    op.execute("DROP FUNCTION guard_auto_work_tariff_approval()")
    op.execute("DROP FUNCTION guard_auto_work_tariff_revision()")
