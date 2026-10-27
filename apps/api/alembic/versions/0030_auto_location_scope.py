"""Optional synthetic service-location scope for staff auto mutations.

Revision ID: 0030_auto_location_scope
Revises: 0029_auto_proof_reissue

No production bootstrap, manager assignment API, branch transfer or PII.
Legacy synthetic fixtures retain old behavior until the explicit feature gate.
"""
from alembic import op

revision = "0030_auto_location_scope"
down_revision = "0029_auto_proof_reissue"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
      CREATE TABLE auto_service_location (
        id uuid PRIMARY KEY,
        code text NOT NULL UNIQUE
          CHECK (code ~ '^[A-Z0-9][A-Z0-9_-]{1,31}$'),
        created_at timestamptz NOT NULL DEFAULT now(),
        disabled_at timestamptz
          CHECK (disabled_at IS NULL OR disabled_at >= created_at)
      )
    """)
    op.execute("""
      CREATE TABLE auto_vehicle_location_binding (
        vehicle_id uuid PRIMARY KEY
          REFERENCES auto_vehicle(id) ON DELETE RESTRICT,
        location_id uuid NOT NULL
          REFERENCES auto_service_location(id) ON DELETE RESTRICT,
        created_at timestamptz NOT NULL DEFAULT now()
      )
    """)
    op.execute("""
      CREATE INDEX ix_auto_vehicle_location_binding_location
      ON auto_vehicle_location_binding(location_id)
    """)
    op.execute("""
      CREATE TABLE auto_location_role_grant (
        id uuid PRIMARY KEY,
        location_id uuid NOT NULL
          REFERENCES auto_service_location(id) ON DELETE RESTRICT,
        user_id uuid NOT NULL REFERENCES app_user(id) ON DELETE RESTRICT,
        role_code text NOT NULL CHECK (role_code IN ('INTAKE','MANAGER')),
        created_at timestamptz NOT NULL DEFAULT now(),
        revoked_at timestamptz
          CHECK (revoked_at IS NULL OR revoked_at >= created_at)
      )
    """)
    op.execute("""
      CREATE UNIQUE INDEX ux_auto_location_role_active
      ON auto_location_role_grant(location_id,user_id,role_code)
      WHERE revoked_at IS NULL
    """)
    op.execute("""
      CREATE FUNCTION guard_auto_service_location()
      RETURNS trigger LANGUAGE plpgsql AS $fn$
      BEGIN
        IF (NEW.id,NEW.code,NEW.created_at)
           IS DISTINCT FROM (OLD.id,OLD.code,OLD.created_at) THEN
          RAISE EXCEPTION 'auto location identity immutable';
        END IF;
        IF OLD.disabled_at IS NOT NULL
           AND NEW.disabled_at IS DISTINCT FROM OLD.disabled_at THEN
          RAISE EXCEPTION 'auto location disable irreversible';
        END IF;
        RETURN NEW;
      END $fn$
    """)
    op.execute("""
      CREATE FUNCTION guard_auto_location_role_grant()
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
      CREATE TRIGGER auto_service_location_guard
      BEFORE UPDATE ON auto_service_location
      FOR EACH ROW EXECUTE FUNCTION guard_auto_service_location()
    """)
    op.execute("""
      CREATE TRIGGER auto_location_role_grant_guard
      BEFORE UPDATE ON auto_location_role_grant
      FOR EACH ROW EXECUTE FUNCTION guard_auto_location_role_grant()
    """)
    # Immutable location membership: no silent transfer between branches.
    # Any future transfer requires a separately audited explicit workflow.
    for table in (
        "auto_service_location",
        "auto_vehicle_location_binding",
        "auto_location_role_grant",
    ):
        op.execute(f"""
          CREATE TRIGGER {table}_no_delete
          BEFORE DELETE ON {table}
          FOR EACH ROW EXECUTE FUNCTION reject_audit_mutation()
        """)
    op.execute("""
      CREATE TRIGGER auto_vehicle_location_binding_immutable
      BEFORE UPDATE ON auto_vehicle_location_binding
      FOR EACH ROW EXECUTE FUNCTION reject_audit_mutation()
    """)


def downgrade():
    # ONLY disposable synthetic PostgreSQL: no approved archival/export yet.
    op.execute("DROP TABLE auto_location_role_grant")
    op.execute("DROP TABLE auto_vehicle_location_binding")
    op.execute("DROP TABLE auto_service_location")
    op.execute("DROP FUNCTION guard_auto_location_role_grant()")
    op.execute("DROP FUNCTION guard_auto_service_location()")
