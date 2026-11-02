"""Immutable synthetic automotive estimate revisions and decisions.

Revision ID: 0086_auto_estimate
Revises: 0085_auto_inspection_qc
"""
from alembic import op

revision = "0086_auto_estimate"
down_revision = "0085_auto_inspection_qc"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
      CREATE TABLE auto_work_order_estimate_revision (
        id uuid PRIMARY KEY,
        work_order_id uuid NOT NULL REFERENCES auto_work_order(id),
        organization_id uuid NOT NULL REFERENCES auto_organization(id),
        location_id uuid NOT NULL REFERENCES auto_service_location(id),
        version integer NOT NULL CHECK (version BETWEEN 1 AND 30),
        actor_id uuid NOT NULL REFERENCES app_user(id),
        request_key uuid NOT NULL,
        fingerprint char(64) NOT NULL,
        currency text NOT NULL DEFAULT 'RUB' CHECK (currency='RUB'),
        total_minor bigint NOT NULL CHECK (total_minor BETWEEN 0 AND 1000000000000),
        service_lines jsonb NOT NULL CHECK (
          jsonb_typeof(service_lines)='array'
          AND jsonb_array_length(service_lines) BETWEEN 1 AND 50
        ),
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        UNIQUE (work_order_id, version),
        UNIQUE (work_order_id, request_key)
      )
    """)
    op.execute("""
      CREATE TABLE auto_work_order_estimate_decision (
        id uuid PRIMARY KEY,
        revision_id uuid NOT NULL UNIQUE REFERENCES auto_work_order_estimate_revision(id),
        work_order_id uuid NOT NULL REFERENCES auto_work_order(id),
        actor_id uuid NOT NULL REFERENCES app_user(id),
        request_key uuid NOT NULL,
        decision text NOT NULL CHECK (decision IN ('APPROVED','DECLINED')),
        fingerprint char(64) NOT NULL,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        UNIQUE (actor_id,request_key)
      )
    """)
    op.execute("""
      CREATE FUNCTION guard_auto_estimate_revision()
      RETURNS trigger LANGUAGE plpgsql AS $fn$
      DECLARE prev_version integer;
      BEGIN
        IF NOT EXISTS (
          SELECT 1 FROM auto_work_order w
          JOIN auto_appointment a ON a.id=w.appointment_id
          JOIN LATERAL (
            SELECT COALESCE((
              SELECT r.new_booking_id FROM auto_appointment_reschedule r
              WHERE r.appointment_id=a.id
              ORDER BY r.created_at DESC,r.id DESC LIMIT 1
            ),a.booking_id) AS current_id
          ) current_booking ON TRUE
          JOIN auto_bay_booking b ON b.id=current_booking.current_id
          JOIN auto_vehicle_customer_link proof ON proof.id=a.customer_link_id
            AND proof.vehicle_id=a.vehicle_id
          JOIN auto_organization o ON o.id=w.organization_id AND o.disabled_at IS NULL
          JOIN auto_service_location loc ON loc.id=w.location_id
            AND loc.organization_id=w.organization_id AND loc.disabled_at IS NULL
          WHERE w.id=NEW.work_order_id AND w.organization_id=NEW.organization_id
            AND w.location_id=NEW.location_id
            AND a.organization_id=w.organization_id AND a.location_id=w.location_id
            AND b.organization_id=w.organization_id AND b.location_id=w.location_id
            AND b.canceled_at IS NULL
            AND proof.verified_at IS NOT NULL AND proof.revoked_at IS NULL
          FOR SHARE OF w,a,b,proof,o,loc
        ) THEN RAISE EXCEPTION 'estimate scope unavailable'; END IF;
        SELECT max(version) INTO prev_version FROM auto_work_order_estimate_revision
          WHERE work_order_id=NEW.work_order_id;
        IF NEW.version<>COALESCE(prev_version,0)+1
          OR EXISTS (
            SELECT 1 FROM auto_work_order_estimate_decision d
            JOIN auto_work_order_estimate_revision r ON r.id=d.revision_id
            WHERE r.work_order_id=NEW.work_order_id
          )
        THEN RAISE EXCEPTION 'estimate finalized or version conflict'; END IF;
        RETURN NEW;
      END $fn$
    """)
    op.execute("""
      CREATE TRIGGER auto_estimate_revision_guard
      BEFORE INSERT ON auto_work_order_estimate_revision
      FOR EACH ROW EXECUTE FUNCTION guard_auto_estimate_revision()
    """)
    op.execute("""
      CREATE FUNCTION guard_auto_estimate_decision()
      RETURNS trigger LANGUAGE plpgsql AS $fn$
      BEGIN
        IF NOT EXISTS (
          SELECT 1 FROM auto_work_order_estimate_revision r
          JOIN auto_work_order w ON w.id=r.work_order_id
          JOIN auto_appointment a ON a.id=w.appointment_id
          JOIN auto_vehicle_customer_link proof ON proof.id=a.customer_link_id
            AND proof.vehicle_id=a.vehicle_id
          JOIN account_customer_link acl ON acl.customer_id=proof.customer_id
            AND acl.user_id=NEW.actor_id AND acl.revoked_at IS NULL
          JOIN user_role role ON role.user_id=NEW.actor_id
            AND role.role_code='CLIENT' AND role.revoked_at IS NULL
            AND (role.expires_at IS NULL OR role.expires_at>now())
          WHERE r.id=NEW.revision_id AND r.work_order_id=NEW.work_order_id
            AND proof.verified_at IS NOT NULL AND proof.revoked_at IS NULL
            AND NOT EXISTS (
              SELECT 1 FROM auto_work_order_estimate_revision newer
              WHERE newer.work_order_id=r.work_order_id AND newer.version>r.version
            )
          FOR SHARE OF r,w,a,proof,acl,role
        ) THEN RAISE EXCEPTION 'estimate decision scope unavailable'; END IF;
        RETURN NEW;
      END $fn$
    """)
    op.execute("""
      CREATE TRIGGER auto_estimate_decision_guard
      BEFORE INSERT ON auto_work_order_estimate_decision
      FOR EACH ROW EXECUTE FUNCTION guard_auto_estimate_decision()
    """)
    for table in ("auto_work_order_estimate_revision", "auto_work_order_estimate_decision"):
        op.execute(f"""CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE
          ON {table} FOR EACH ROW EXECUTE FUNCTION immutable_auto_stock_event()""")
        op.execute(f"REVOKE ALL ON {table} FROM PUBLIC")


def downgrade():
    # Only disposable synthetic databases; preserves no real accounting evidence.
    op.execute("DROP TABLE auto_work_order_estimate_decision")
    op.execute("DROP TABLE auto_work_order_estimate_revision")
    op.execute("DROP FUNCTION guard_auto_estimate_decision()")
    op.execute("DROP FUNCTION guard_auto_estimate_revision()")
