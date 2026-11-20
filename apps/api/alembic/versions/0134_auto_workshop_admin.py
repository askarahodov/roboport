"""Защищённое управление филиалами, постами и сменами в synthetic-контуре.

Revision ID: 0134_auto_workshop_admin
Revises: 0133_auto_vehicle_link_guard

Операции доступны только через закрытый ADMIN API; не создают production identity.
"""
from alembic import op

revision = "0134_auto_workshop_admin"
down_revision = "0133_auto_vehicle_link_guard"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE auto_create_receipt DROP CONSTRAINT auto_create_receipt_operation_check")
    op.execute("""
      ALTER TABLE auto_create_receipt ADD CONSTRAINT auto_create_receipt_operation_check
      CHECK (operation IN (
        'vehicle.link.propose','vehicle.intake.grant',
        'workshop.location.create','workshop.bay.create','workshop.shift.create'
      ))
    """)
    op.execute("""
      CREATE TABLE auto_workshop_admin_action (
        id uuid PRIMARY KEY,
        organization_id uuid NOT NULL REFERENCES auto_organization(id),
        location_id uuid NOT NULL REFERENCES auto_service_location(id),
        actor_id uuid NOT NULL REFERENCES app_user(id),
        entity_type text NOT NULL CHECK (entity_type IN ('LOCATION','BAY','SHIFT')),
        entity_id uuid NOT NULL,
        action text NOT NULL CHECK (action IN (
          'LOCATION_CREATED','LOCATION_DISABLED',
          'BAY_CREATED','BAY_DISABLED',
          'SHIFT_CREATED','SHIFT_CANCELED'
        )),
        created_at timestamptz NOT NULL DEFAULT now()
      )
    """)
    op.execute("""
      CREATE INDEX ix_auto_workshop_admin_action_org_time
      ON auto_workshop_admin_action(organization_id,created_at DESC,id DESC)
    """)
    op.execute("""
      CREATE TRIGGER auto_workshop_admin_action_immutable
      BEFORE UPDATE OR DELETE ON auto_workshop_admin_action
      FOR EACH ROW EXECUTE FUNCTION reject_audit_mutation()
    """)
    op.execute("""
      CREATE TRIGGER auto_workshop_admin_action_no_truncate
      BEFORE TRUNCATE ON auto_workshop_admin_action
      FOR EACH STATEMENT EXECUTE FUNCTION reject_audit_mutation()
    """)
    op.execute("REVOKE ALL ON auto_workshop_admin_action FROM PUBLIC")

    # Serialize branch creation/disable with bay and shift creation. The
    # previous guard checked branch membership but did not lock the branch row.
    op.execute("""
      CREATE OR REPLACE FUNCTION guard_auto_workshop()
      RETURNS trigger LANGUAGE plpgsql AS $fn$
      BEGIN
        IF TG_OP = 'DELETE' THEN
          RAISE EXCEPTION 'workshop history deletion forbidden';
        END IF;

        IF TG_OP = 'INSERT' THEN
          PERFORM 1
          FROM auto_service_location l
          JOIN auto_organization o ON o.id=l.organization_id
          WHERE l.id=NEW.location_id
            AND l.organization_id=NEW.organization_id
            AND l.disabled_at IS NULL AND o.disabled_at IS NULL
          FOR SHARE OF l,o;
          IF NOT FOUND THEN
            RAISE EXCEPTION 'location outside enabled organization';
          END IF;
          RETURN NEW;
        END IF;

        IF TG_TABLE_NAME = 'auto_work_bay' THEN
          IF (NEW.id,NEW.organization_id,NEW.location_id,NEW.code,NEW.created_at)
             IS DISTINCT FROM
             (OLD.id,OLD.organization_id,OLD.location_id,OLD.code,OLD.created_at)
             OR (OLD.disabled_at IS NOT NULL
                 AND NEW.disabled_at IS DISTINCT FROM OLD.disabled_at) THEN
            RAISE EXCEPTION 'bay identity/disable irreversible';
          END IF;
          IF OLD.disabled_at IS NULL AND NEW.disabled_at IS NOT NULL
             AND EXISTS (
               SELECT 1 FROM auto_bay_booking b
               WHERE b.bay_id=OLD.id AND b.organization_id=OLD.organization_id
                 AND b.location_id=OLD.location_id AND b.canceled_at IS NULL
                 AND b.ends_at>clock_timestamp()
             ) THEN
            RAISE EXCEPTION 'bay has active future bookings';
          END IF;
        ELSE
          IF (NEW.id,NEW.organization_id,NEW.location_id,NEW.user_id,
              NEW.starts_at,NEW.ends_at,NEW.created_at)
             IS DISTINCT FROM
             (OLD.id,OLD.organization_id,OLD.location_id,OLD.user_id,
              OLD.starts_at,OLD.ends_at,OLD.created_at)
             OR (OLD.canceled_at IS NOT NULL
                 AND NEW.canceled_at IS DISTINCT FROM OLD.canceled_at) THEN
            RAISE EXCEPTION 'shift identity/cancellation irreversible';
          END IF;
          IF OLD.canceled_at IS NULL AND NEW.canceled_at IS NOT NULL
             AND EXISTS (
               SELECT 1 FROM auto_employee_reservation r
               WHERE r.organization_id=OLD.organization_id
                 AND r.location_id=OLD.location_id
                 AND r.employee_id=OLD.user_id AND r.canceled_at IS NULL
                 AND r.starts_at<OLD.ends_at AND r.ends_at>OLD.starts_at
             ) THEN
            RAISE EXCEPTION 'shift has active employee reservations';
          END IF;
        END IF;
        RETURN NEW;
      END $fn$
    """)

    # Branch retirement is validated by the ADMIN service while it holds
    # FOR UPDATE on auto_service_location. Every branch-scoped INSERT guard
    # takes FOR SHARE on that same row: creation either commits before the
    # service checks dependencies, or observes the disabled branch afterward.
    # Keep cross-table policy in one service so conflicts map to stable 409s;
    # do not duplicate the policy in a trigger.

    # Lock branch rows on staff-grant inserts so branch deactivation cannot
    # race with a new organization-scoped assignment.
    op.execute("""
      CREATE OR REPLACE FUNCTION auto_organization_grant_guard()
      RETURNS trigger LANGUAGE plpgsql AS $fn$
      BEGIN
        IF TG_OP = 'DELETE' THEN
          RAISE EXCEPTION 'grant deletion forbidden';
        END IF;
        IF TG_OP = 'INSERT' THEN
          PERFORM 1 FROM auto_organization o
            WHERE o.id=NEW.organization_id AND o.disabled_at IS NULL
            FOR SHARE;
          IF NOT FOUND THEN RAISE EXCEPTION 'organization unavailable'; END IF;
          IF NEW.location_id IS NOT NULL THEN
            PERFORM 1 FROM auto_service_location l
              WHERE l.id=NEW.location_id
                AND l.organization_id=NEW.organization_id
                AND l.disabled_at IS NULL FOR SHARE;
            IF NOT FOUND THEN RAISE EXCEPTION 'location organization mismatch'; END IF;
          END IF;
        ELSE
          IF (NEW.id,NEW.organization_id,NEW.location_id,NEW.user_id,
              NEW.role_code,NEW.created_at)
             IS DISTINCT FROM
             (OLD.id,OLD.organization_id,OLD.location_id,OLD.user_id,
              OLD.role_code,OLD.created_at) THEN
            RAISE EXCEPTION 'grant identity immutable';
          END IF;
          IF OLD.revoked_at IS NOT NULL
             AND NEW.revoked_at IS DISTINCT FROM OLD.revoked_at THEN
            RAISE EXCEPTION 'revocation irreversible';
          END IF;
        END IF;
        RETURN NEW;
      END $fn$
    """)

    # The legacy branch grant table is retained for historical fixtures, but
    # new inserts must also serialize with branch deactivation.
    op.execute("DROP TRIGGER auto_location_role_grant_guard ON auto_location_role_grant")
    op.execute("""
      CREATE OR REPLACE FUNCTION guard_auto_location_role_grant()
      RETURNS trigger LANGUAGE plpgsql AS $fn$
      BEGIN
        IF TG_OP='INSERT' THEN
          PERFORM 1 FROM auto_service_location
            WHERE id=NEW.location_id AND disabled_at IS NULL FOR SHARE;
          IF NOT FOUND THEN RAISE EXCEPTION 'location unavailable'; END IF;
          RETURN NEW;
        END IF;
        IF (NEW.id,NEW.location_id,NEW.user_id,NEW.role_code,NEW.created_at)
           IS DISTINCT FROM
           (OLD.id,OLD.location_id,OLD.user_id,OLD.role_code,OLD.created_at) THEN
          RAISE EXCEPTION 'auto location grant identity immutable';
        END IF;
        IF OLD.revoked_at IS NOT NULL
           AND NEW.revoked_at IS DISTINCT FROM OLD.revoked_at THEN
          RAISE EXCEPTION 'auto location grant revocation irreversible';
        END IF;
        RETURN NEW;
      END $fn$
    """)
    op.execute("""
      CREATE TRIGGER auto_location_role_grant_guard
      BEFORE INSERT OR UPDATE ON auto_location_role_grant
      FOR EACH ROW EXECUTE FUNCTION guard_auto_location_role_grant()
    """)

    # Vehicle-to-branch bindings are immutable; new bindings must not race
    # branch deactivation.
    op.execute("""
      CREATE FUNCTION guard_auto_vehicle_location_binding_insert()
      RETURNS trigger LANGUAGE plpgsql AS $fn$
      BEGIN
        PERFORM 1 FROM auto_service_location
          WHERE id=NEW.location_id AND disabled_at IS NULL FOR SHARE;
        IF NOT FOUND THEN RAISE EXCEPTION 'location unavailable'; END IF;
        RETURN NEW;
      END $fn$
    """)
    op.execute("""
      CREATE TRIGGER auto_vehicle_location_binding_scope
      BEFORE INSERT ON auto_vehicle_location_binding
      FOR EACH ROW EXECUTE FUNCTION guard_auto_vehicle_location_binding_insert()
    """)


def downgrade():
    op.execute("DROP TRIGGER auto_vehicle_location_binding_scope ON auto_vehicle_location_binding")
    op.execute("DROP FUNCTION guard_auto_vehicle_location_binding_insert()")
    op.execute("DROP TRIGGER auto_location_role_grant_guard ON auto_location_role_grant")
    op.execute("""
      CREATE OR REPLACE FUNCTION guard_auto_location_role_grant()
      RETURNS trigger LANGUAGE plpgsql AS $fn$
      BEGIN
        IF (NEW.id,NEW.location_id,NEW.user_id,NEW.role_code,NEW.created_at)
           IS DISTINCT FROM
           (OLD.id,OLD.location_id,OLD.user_id,OLD.role_code,OLD.created_at) THEN
          RAISE EXCEPTION 'auto location grant identity immutable';
        END IF;
        IF OLD.revoked_at IS NOT NULL
           AND NEW.revoked_at IS DISTINCT FROM OLD.revoked_at THEN
          RAISE EXCEPTION 'auto location grant revocation irreversible';
        END IF;
        RETURN NEW;
      END $fn$
    """)
    op.execute("""
      CREATE TRIGGER auto_location_role_grant_guard
      BEFORE UPDATE ON auto_location_role_grant
      FOR EACH ROW EXECUTE FUNCTION guard_auto_location_role_grant()
    """)
    op.execute("""
      DO $body$
      BEGIN
        IF EXISTS (SELECT 1 FROM auto_create_receipt
                   WHERE operation IN ('workshop.location.create',
                     'workshop.bay.create','workshop.shift.create')) THEN
          RAISE EXCEPTION 'workshop idempotency receipts prevent downgrade';
        END IF;
      END;
      $body$;
    """)
    op.execute("ALTER TABLE auto_create_receipt DROP CONSTRAINT auto_create_receipt_operation_check")
    op.execute("""
      ALTER TABLE auto_create_receipt ADD CONSTRAINT auto_create_receipt_operation_check
      CHECK (operation IN ('vehicle.link.propose','vehicle.intake.grant'))
    """)
    op.execute("DROP TABLE auto_workshop_admin_action")

    # Restore the original organization grant guard for disposable PG17 only.
    op.execute("""
      CREATE OR REPLACE FUNCTION auto_organization_grant_guard()
      RETURNS trigger LANGUAGE plpgsql AS $$
      BEGIN
        IF TG_OP = 'DELETE' THEN
          RAISE EXCEPTION 'grant deletion forbidden';
        END IF;
        IF TG_OP = 'INSERT' THEN
          IF NOT EXISTS (SELECT 1 FROM auto_organization o
                         WHERE o.id=NEW.organization_id AND o.disabled_at IS NULL) THEN
            RAISE EXCEPTION 'organization unavailable';
          END IF;
          IF NEW.location_id IS NOT NULL AND NOT EXISTS
             (SELECT 1 FROM auto_service_location l
              WHERE l.id=NEW.location_id AND l.organization_id=NEW.organization_id
                AND l.disabled_at IS NULL) THEN
            RAISE EXCEPTION 'location organization mismatch';
          END IF;
        ELSE
          IF (NEW.id,NEW.organization_id,NEW.location_id,NEW.user_id,
              NEW.role_code,NEW.created_at)
             IS DISTINCT FROM
             (OLD.id,OLD.organization_id,OLD.location_id,OLD.user_id,
              OLD.role_code,OLD.created_at) THEN
            RAISE EXCEPTION 'grant identity immutable';
          END IF;
          IF OLD.revoked_at IS NOT NULL AND
             NEW.revoked_at IS DISTINCT FROM OLD.revoked_at THEN
            RAISE EXCEPTION 'revocation irreversible';
          END IF;
        END IF;
        RETURN NEW;
      END $$
    """)

    # Reinstall the pre-0134 workshop guard for disposable PG17 only.
    op.execute("""
      CREATE OR REPLACE FUNCTION guard_auto_workshop()
      RETURNS trigger LANGUAGE plpgsql AS $fn$
      BEGIN
        IF TG_OP = 'DELETE' THEN
          RAISE EXCEPTION 'workshop history deletion forbidden';
        END IF;
        IF TG_TABLE_NAME = 'auto_work_bay' THEN
          IF TG_OP = 'UPDATE' THEN
            IF (NEW.id,NEW.organization_id,NEW.location_id,NEW.code,NEW.created_at)
               IS DISTINCT FROM
               (OLD.id,OLD.organization_id,OLD.location_id,OLD.code,OLD.created_at)
               OR (OLD.disabled_at IS NOT NULL AND
                   NEW.disabled_at IS DISTINCT FROM OLD.disabled_at) THEN
              RAISE EXCEPTION 'bay identity/disable irreversible';
            END IF;
          END IF;
        ELSE
          IF TG_OP = 'UPDATE' THEN
            IF (NEW.id,NEW.organization_id,NEW.location_id,NEW.user_id,
                NEW.starts_at,NEW.ends_at,NEW.created_at)
               IS DISTINCT FROM
               (OLD.id,OLD.organization_id,OLD.location_id,OLD.user_id,
                OLD.starts_at,OLD.ends_at,OLD.created_at)
               OR (OLD.canceled_at IS NOT NULL AND
                   NEW.canceled_at IS DISTINCT FROM OLD.canceled_at) THEN
              RAISE EXCEPTION 'shift identity/cancellation irreversible';
            END IF;
          END IF;
        END IF;
        IF TG_OP = 'INSERT' AND NOT EXISTS (
          SELECT 1 FROM auto_service_location l
          JOIN auto_organization o ON o.id=l.organization_id
          WHERE l.id=NEW.location_id AND l.organization_id=NEW.organization_id
            AND l.disabled_at IS NULL AND o.disabled_at IS NULL
        ) THEN
          RAISE EXCEPTION 'location outside enabled organization';
        END IF;
        RETURN NEW;
      END $fn$
    """)
