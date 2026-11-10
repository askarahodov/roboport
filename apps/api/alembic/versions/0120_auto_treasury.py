"""Synthetic branch treasury: cashier accounts, immutable advances/refunds and day close.

Revision ID: 0120_auto_treasury
Revises: 0119_auto_document_acl
Strictly no real money, card/bank callback, receipts or legal fiscal records.
"""
from alembic import op

revision="0120_auto_treasury"
down_revision="0119_auto_document_acl"
branch_labels=None
depends_on=None


def upgrade():
    op.execute("""
      ALTER TABLE auto_organization_role_grant
        DROP CONSTRAINT auto_organization_role_grant_role_code_check
    """)
    op.execute("""
      ALTER TABLE auto_organization_role_grant
        ADD CONSTRAINT auto_organization_role_grant_role_code_check
        CHECK (role_code IN ('ADMIN','MANAGER','INTAKE','MECHANIC','WAREHOUSE','CASHIER'))
    """)
    op.execute("""
      CREATE TABLE auto_treasury_account (
        id uuid PRIMARY KEY,
        organization_id uuid NOT NULL REFERENCES auto_organization(id),
        location_id uuid NOT NULL REFERENCES auto_service_location(id),
        code text NOT NULL CHECK (code ~ '^[A-Z0-9_-]{2,32}$'),
        label text NOT NULL CHECK(length(btrim(label)) BETWEEN 3 AND 80),
        kind text NOT NULL CHECK (kind IN ('CASH_SIM','BANK_SIM')),
        currency text NOT NULL DEFAULT 'RUB' CHECK(currency='RUB'),
        actor_id uuid NOT NULL REFERENCES app_user(id),
        request_key uuid NOT NULL UNIQUE,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        UNIQUE (organization_id,location_id,code)
      )
    """)
    op.execute("""
      CREATE TABLE auto_treasury_event (
        id uuid PRIMARY KEY,
        account_id uuid NOT NULL REFERENCES auto_treasury_account(id),
        work_order_id uuid NOT NULL REFERENCES auto_work_order(id),
        revision_id uuid NOT NULL REFERENCES auto_work_order_estimate_revision(id),
        actor_id uuid NOT NULL REFERENCES app_user(id),
        request_key uuid NOT NULL UNIQUE,
        kind text NOT NULL CHECK(kind IN
          ('ADVANCE','PAYMENT','APPLY_ADVANCE','REFUND_ADVANCE','REFUND_PAYMENT')),
        amount_minor bigint NOT NULL CHECK(amount_minor BETWEEN 1 AND 1000000000000),
        reason text NOT NULL CHECK(length(btrim(reason)) BETWEEN 3 AND 160),
        business_day date NOT NULL,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp()
      )
    """)
    op.execute("""CREATE INDEX auto_treasury_order_events ON auto_treasury_event
                  (work_order_id,created_at,id)""")
    op.execute("""CREATE INDEX auto_treasury_account_events ON auto_treasury_event
                  (account_id,business_day,created_at,id)""")
    op.execute("""
      CREATE TABLE auto_treasury_day_close (
        id uuid PRIMARY KEY,
        account_id uuid NOT NULL REFERENCES auto_treasury_account(id),
        business_day date NOT NULL,
        actor_id uuid NOT NULL REFERENCES app_user(id),
        request_key uuid NOT NULL UNIQUE,
        opening_minor bigint NOT NULL CHECK(opening_minor BETWEEN 0 AND 1000000000000000),
        incoming_minor bigint NOT NULL CHECK(incoming_minor BETWEEN 0 AND 1000000000000000),
        outgoing_minor bigint NOT NULL CHECK(outgoing_minor BETWEEN 0 AND 1000000000000000),
        closing_minor bigint NOT NULL CHECK(closing_minor BETWEEN 0 AND 1000000000000000),
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        UNIQUE(account_id,business_day),
        CHECK(opening_minor+incoming_minor-outgoing_minor=closing_minor)
      )
    """)
    op.execute("""
      CREATE FUNCTION guard_auto_treasury_account()
      RETURNS trigger LANGUAGE plpgsql AS $f$
      BEGIN
        IF NOT EXISTS (
          SELECT 1 FROM auto_organization o
          JOIN auto_service_location l
            ON l.organization_id=o.id AND l.id=NEW.location_id
            AND l.disabled_at IS NULL
          JOIN auto_organization_role_grant g
            ON g.organization_id=o.id AND g.user_id=NEW.actor_id
            AND g.role_code='MANAGER' AND g.revoked_at IS NULL
            AND (g.location_id=l.id OR g.location_id IS NULL)
          JOIN app_user u ON u.id=NEW.actor_id AND u.status='ACTIVE'
          WHERE o.id=NEW.organization_id AND o.disabled_at IS NULL
          FOR SHARE OF o,l,g,u
        ) THEN RAISE EXCEPTION 'treasury account manager/scope required'; END IF;
        RETURN NEW;
      END $f$
    """)
    op.execute("""CREATE TRIGGER auto_treasury_account_guard
        BEFORE INSERT ON auto_treasury_account FOR EACH ROW
        EXECUTE FUNCTION guard_auto_treasury_account()""")
    op.execute("""
      CREATE FUNCTION guard_auto_treasury_event()
      RETURNS trigger LANGUAGE plpgsql AS $f$
      DECLARE tenant uuid; branch uuid; timezone_name text; total numeric;
      DECLARE paid numeric; advances numeric; refund_paid numeric;
      DECLARE refund_advance numeric; applied numeric; account_paid numeric;
      DECLARE account_advances numeric; account_refund_paid numeric;
      DECLARE account_refund_advance numeric; account_applied numeric;
      DECLARE cash_balance numeric; latest_revision uuid;
      BEGIN
        -- WorkOrder lock serializes price, customer proof, booking and money
        -- transitions; account lock serializes cross-order balance/day close.
        SELECT w.organization_id,w.location_id,loc.timezone_name
          INTO tenant,branch,timezone_name
        FROM auto_work_order w
        JOIN auto_appointment a ON a.id=w.appointment_id
          AND a.organization_id=w.organization_id AND a.location_id=w.location_id
        JOIN auto_vehicle_customer_link proof ON proof.id=a.customer_link_id
          AND proof.vehicle_id=a.vehicle_id
          AND proof.verified_at IS NOT NULL AND proof.revoked_at IS NULL
        JOIN auto_vehicle_location_binding binding ON binding.vehicle_id=a.vehicle_id
          AND binding.location_id=w.location_id
        JOIN auto_organization org ON org.id=w.organization_id AND org.disabled_at IS NULL
        JOIN auto_service_location loc ON loc.id=w.location_id
          AND loc.organization_id=org.id AND loc.disabled_at IS NULL
        JOIN LATERAL (
          SELECT COALESCE((
            SELECT move.new_booking_id FROM auto_appointment_reschedule move
            WHERE move.appointment_id=a.id
            ORDER BY move.created_at DESC,move.id DESC LIMIT 1
          ),a.booking_id) AS booking_id
        ) current_booking ON TRUE
        JOIN auto_bay_booking bay ON bay.id=current_booking.booking_id
          AND bay.organization_id=w.organization_id AND bay.location_id=w.location_id
          AND bay.canceled_at IS NULL
        WHERE w.id=NEW.work_order_id
        FOR UPDATE OF w;
        IF tenant IS NULL THEN RAISE EXCEPTION 'treasury order proof unavailable'; END IF;
        IF NOT EXISTS(
          SELECT 1 FROM auto_treasury_account a
          WHERE a.id=NEW.account_id AND a.organization_id=tenant
            AND a.location_id=branch
          FOR UPDATE
        ) THEN RAISE EXCEPTION 'treasury account mismatch'; END IF;
        IF NOT EXISTS(
          SELECT 1 FROM auto_organization_role_grant g
          JOIN app_user u ON u.id=g.user_id AND u.status='ACTIVE'
          WHERE g.organization_id=tenant AND g.user_id=NEW.actor_id
            AND g.role_code='CASHIER' AND g.revoked_at IS NULL
            AND (g.location_id=branch OR g.location_id IS NULL)
          FOR SHARE OF g,u
        ) THEN RAISE EXCEPTION 'treasury CASHIER required'; END IF;
        IF NEW.business_day IS DISTINCT FROM (clock_timestamp() AT TIME ZONE timezone_name)::date
          OR abs(extract(epoch FROM NEW.created_at-clock_timestamp()))>10
        THEN RAISE EXCEPTION 'treasury event date forged'; END IF;
        IF EXISTS(SELECT 1 FROM auto_treasury_day_close c
          WHERE c.account_id=NEW.account_id AND c.business_day>=NEW.business_day)
        THEN RAISE EXCEPTION 'treasury day closed'; END IF;
        IF EXISTS(SELECT 1 FROM auto_order_demo_document_event d
          WHERE d.work_order_id=NEW.work_order_id AND d.action='CLOSED_TECHNICAL')
        THEN RAISE EXCEPTION 'document already closed'; END IF;
        SELECT r.id INTO latest_revision
        FROM auto_work_order_estimate_revision r
        JOIN auto_work_order_estimate_decision d
          ON d.revision_id=r.id AND d.decision='APPROVED'
        WHERE r.work_order_id=NEW.work_order_id AND
              r.version=(SELECT max(x.version)
                FROM auto_work_order_estimate_revision x
                WHERE x.work_order_id=r.work_order_id);
        total:=auto_demo_billable_total(NEW.work_order_id,latest_revision);
        IF total IS NULL OR latest_revision IS DISTINCT FROM NEW.revision_id
        THEN RAISE EXCEPTION 'treasury approved latest price required'; END IF;
        IF NEW.kind IN ('PAYMENT','APPLY_ADVANCE','REFUND_PAYMENT') AND NOT EXISTS (
          SELECT 1 FROM auto_work_order_execution e
          JOIN auto_work_order_execution_finish f ON f.execution_id=e.id
          JOIN auto_work_order_qc_event qc ON qc.work_order_id=e.work_order_id
            AND qc.result='PASS' AND qc.created_at>=f.created_at
          WHERE e.work_order_id=NEW.work_order_id AND e.revision_id=latest_revision
            AND NOT EXISTS(SELECT 1 FROM auto_work_order_qc_event newer
              WHERE newer.work_order_id=qc.work_order_id
                AND (newer.created_at,newer.id)>(qc.created_at,qc.id))
        ) THEN RAISE EXCEPTION 'treasury independent QC required'; END IF;
        SELECT COALESCE(sum(amount_minor) FILTER(WHERE kind='PAYMENT'),0),
               COALESCE(sum(amount_minor) FILTER(WHERE kind='ADVANCE'),0),
               COALESCE(sum(amount_minor) FILTER(WHERE kind='REFUND_PAYMENT'),0),
               COALESCE(sum(amount_minor) FILTER(WHERE kind='REFUND_ADVANCE'),0),
               COALESCE(sum(amount_minor) FILTER(WHERE kind='APPLY_ADVANCE'),0)
          INTO paid,advances,refund_paid,refund_advance,applied
        FROM auto_treasury_event WHERE work_order_id=NEW.work_order_id;
        SELECT COALESCE(sum(amount_minor) FILTER(WHERE kind='PAYMENT'),0),
               COALESCE(sum(amount_minor) FILTER(WHERE kind='ADVANCE'),0),
               COALESCE(sum(amount_minor) FILTER(WHERE kind='REFUND_PAYMENT'),0),
               COALESCE(sum(amount_minor) FILTER(WHERE kind='REFUND_ADVANCE'),0),
               COALESCE(sum(amount_minor) FILTER(WHERE kind='APPLY_ADVANCE'),0)
          INTO account_paid,account_advances,account_refund_paid,
               account_refund_advance,account_applied
        FROM auto_treasury_event
        WHERE work_order_id=NEW.work_order_id AND account_id=NEW.account_id;
        IF (NEW.kind='APPLY_ADVANCE' AND
              NEW.amount_minor>account_advances-account_applied-account_refund_advance)
          OR (NEW.kind='REFUND_ADVANCE' AND
              NEW.amount_minor>account_advances-account_applied-account_refund_advance)
          OR (NEW.kind='REFUND_PAYMENT' AND
              NEW.amount_minor>account_paid+account_applied-account_refund_paid)
        THEN RAISE EXCEPTION 'treasury refund or allocation exceeds source'; END IF;
        IF (paid+applied-refund_paid +
              CASE WHEN NEW.kind IN ('PAYMENT','APPLY_ADVANCE') THEN NEW.amount_minor
                   WHEN NEW.kind='REFUND_PAYMENT' THEN -NEW.amount_minor ELSE 0 END)<0
          OR (advances-applied-refund_advance +
              CASE WHEN NEW.kind='ADVANCE' THEN NEW.amount_minor
                   WHEN NEW.kind IN ('APPLY_ADVANCE','REFUND_ADVANCE')
                     THEN -NEW.amount_minor ELSE 0 END)<0
          OR (paid+advances-refund_paid-refund_advance +
              CASE WHEN NEW.kind IN ('PAYMENT','ADVANCE') THEN NEW.amount_minor
                   WHEN NEW.kind IN ('REFUND_PAYMENT','REFUND_ADVANCE')
                     THEN -NEW.amount_minor ELSE 0 END)>total
        THEN RAISE EXCEPTION 'treasury approved total exceeded'; END IF;
        SELECT COALESCE(sum(CASE WHEN kind IN ('PAYMENT','ADVANCE') THEN amount_minor
                             WHEN kind IN ('REFUND_PAYMENT','REFUND_ADVANCE')
                               THEN -amount_minor ELSE 0 END),0)
          INTO cash_balance FROM auto_treasury_event WHERE account_id=NEW.account_id;
        IF (NEW.kind IN ('REFUND_PAYMENT','REFUND_ADVANCE') AND
            NEW.amount_minor>cash_balance)
            OR cash_balance+NEW.amount_minor>1000000000000000
        THEN RAISE EXCEPTION 'treasury account cash bound'; END IF;
        RETURN NEW;
      END $f$
    """)
    op.execute("""CREATE TRIGGER auto_treasury_event_guard
        BEFORE INSERT ON auto_treasury_event FOR EACH ROW
        EXECUTE FUNCTION guard_auto_treasury_event()""")
    op.execute("""
      CREATE FUNCTION guard_auto_treasury_day_close()
      RETURNS trigger LANGUAGE plpgsql AS $f$
      DECLARE tenant uuid; branch uuid; timezone_name text;
      DECLARE expected_open numeric; expected_in numeric; expected_out numeric;
      BEGIN
        SELECT a.organization_id,a.location_id,loc.timezone_name
          INTO tenant,branch,timezone_name
        FROM auto_treasury_account a
        JOIN auto_organization org ON org.id=a.organization_id
          AND org.disabled_at IS NULL
        JOIN auto_service_location loc ON loc.id=a.location_id
          AND loc.organization_id=org.id AND loc.disabled_at IS NULL
        WHERE a.id=NEW.account_id FOR UPDATE OF a;
        IF tenant IS NULL THEN RAISE EXCEPTION 'treasury account missing'; END IF;
        IF NOT EXISTS(SELECT 1 FROM auto_organization_role_grant g
             JOIN app_user u ON u.id=g.user_id AND u.status='ACTIVE'
             WHERE g.organization_id=tenant AND g.user_id=NEW.actor_id
               AND g.role_code='CASHIER' AND g.revoked_at IS NULL
               AND (g.location_id=branch OR g.location_id IS NULL)
             FOR SHARE OF g,u)
        THEN RAISE EXCEPTION 'treasury cashier close denied'; END IF;
        IF NEW.business_day>(clock_timestamp() AT TIME ZONE timezone_name)::date
          OR abs(extract(epoch FROM NEW.created_at-clock_timestamp()))>10
          OR EXISTS(SELECT 1 FROM auto_treasury_day_close old
               WHERE old.account_id=NEW.account_id AND old.business_day>=NEW.business_day)
          OR EXISTS(SELECT 1 FROM auto_treasury_event e
               WHERE e.account_id=NEW.account_id AND e.business_day<NEW.business_day
                 AND NOT EXISTS(SELECT 1 FROM auto_treasury_day_close old
                   WHERE old.account_id=e.account_id AND old.business_day=e.business_day))
        THEN RAISE EXCEPTION 'treasury close ordering/date invalid'; END IF;
        SELECT
          COALESCE(sum(CASE WHEN business_day<NEW.business_day AND
              kind IN ('PAYMENT','ADVANCE') THEN amount_minor
              WHEN business_day<NEW.business_day AND
              kind IN ('REFUND_PAYMENT','REFUND_ADVANCE') THEN -amount_minor
              ELSE 0 END),0),
          COALESCE(sum(amount_minor) FILTER(WHERE
               business_day=NEW.business_day AND kind IN ('PAYMENT','ADVANCE')),0),
          COALESCE(sum(amount_minor) FILTER(WHERE
               business_day=NEW.business_day AND kind IN
               ('REFUND_PAYMENT','REFUND_ADVANCE')),0)
        INTO expected_open,expected_in,expected_out
        FROM auto_treasury_event WHERE account_id=NEW.account_id;
        IF (NEW.opening_minor,NEW.incoming_minor,NEW.outgoing_minor,NEW.closing_minor)
           IS DISTINCT FROM
           (expected_open,expected_in,expected_out,expected_open+expected_in-expected_out)
        THEN RAISE EXCEPTION 'treasury close totals mismatch'; END IF;
        RETURN NEW;
      END $f$
    """)
    op.execute("""CREATE TRIGGER auto_treasury_day_close_guard
         BEFORE INSERT ON auto_treasury_day_close FOR EACH ROW
         EXECUTE FUNCTION guard_auto_treasury_day_close()""")
    for table in ("auto_treasury_account","auto_treasury_event","auto_treasury_day_close"):
        op.execute(f"""CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table}
           FOR EACH ROW EXECUTE FUNCTION immutable_auto_stock_event()""")
        op.execute(f"REVOKE ALL ON {table} FROM PUBLIC")


def downgrade():
    # Only on a disposable synthetic database: journal history is intentionally immutable.
    op.execute("DROP TABLE auto_treasury_day_close")
    op.execute("DROP FUNCTION guard_auto_treasury_day_close()")
    op.execute("DROP TABLE auto_treasury_event")
    op.execute("DROP FUNCTION guard_auto_treasury_event()")
    op.execute("DROP TABLE auto_treasury_account")
    op.execute("DROP FUNCTION guard_auto_treasury_account()")
    op.execute("ALTER TABLE auto_organization_role_grant DROP CONSTRAINT auto_organization_role_grant_role_code_check")
    op.execute("""ALTER TABLE auto_organization_role_grant
       ADD CONSTRAINT auto_organization_role_grant_role_code_check
       CHECK(role_code IN ('ADMIN','MANAGER','INTAKE','MECHANIC','WAREHOUSE'))""")
