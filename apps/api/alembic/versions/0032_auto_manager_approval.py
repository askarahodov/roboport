"""Двухстороннее подтверждение синтетического назначения MANAGER.

Revision ID: 0032_auto_manager_approval
Revises: 0031_auto_peer_throttle

Не создаёт публичный API, организацию или первого ADMIN.
"""
from alembic import op

revision = "0032_auto_manager_approval"
down_revision = "0031_auto_peer_throttle"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
      CREATE TABLE auto_manager_approval_request (
        id uuid PRIMARY KEY,
        vehicle_id uuid NOT NULL REFERENCES auto_vehicle(id) ON DELETE RESTRICT,
        location_id uuid NOT NULL REFERENCES auto_service_location(id) ON DELETE RESTRICT,
        target_id uuid NOT NULL REFERENCES app_user(id) ON DELETE RESTRICT,
        initiated_by uuid NOT NULL REFERENCES app_user(id) ON DELETE RESTRICT,
        created_at timestamptz NOT NULL DEFAULT now(),
        expires_at timestamptz NOT NULL DEFAULT (now()+interval '30 minutes'),
        approved_by uuid REFERENCES app_user(id) ON DELETE RESTRICT,
        approved_at timestamptz,
        CHECK (initiated_by <> target_id),
        CHECK (expires_at > created_at),
        CHECK ((approved_by IS NULL) = (approved_at IS NULL)),
        CHECK (approved_by IS NULL OR (
          approved_by <> initiated_by AND approved_by <> target_id
        ))
      )
    """)
    op.execute("""
      CREATE INDEX ix_auto_manager_request_vehicle
      ON auto_manager_approval_request(vehicle_id, target_id, created_at DESC)
    """)
    op.execute("""
      CREATE FUNCTION guard_auto_manager_approval_request()
      RETURNS trigger LANGUAGE plpgsql AS $fn$
      BEGIN
        IF (NEW.id,NEW.vehicle_id,NEW.location_id,NEW.target_id,
            NEW.initiated_by,NEW.created_at,NEW.expires_at)
           IS DISTINCT FROM
           (OLD.id,OLD.vehicle_id,OLD.location_id,OLD.target_id,
            OLD.initiated_by,OLD.created_at,OLD.expires_at) THEN
          RAISE EXCEPTION 'manager approval request identity immutable';
        END IF;
        IF OLD.approved_at IS NOT NULL AND NEW IS DISTINCT FROM OLD THEN
          RAISE EXCEPTION 'approved manager request immutable';
        END IF;
        RETURN NEW;
      END $fn$
    """)
    op.execute("""
      CREATE TRIGGER auto_manager_approval_request_guard
      BEFORE UPDATE ON auto_manager_approval_request
      FOR EACH ROW EXECUTE FUNCTION guard_auto_manager_approval_request()
    """)
    op.execute("""
      CREATE TRIGGER auto_manager_approval_request_no_delete
      BEFORE DELETE ON auto_manager_approval_request
      FOR EACH ROW EXECUTE FUNCTION reject_audit_mutation()
    """)
    op.execute("""
      CREATE TABLE auto_manager_approval_audit (
        id uuid PRIMARY KEY,
        request_id uuid NOT NULL REFERENCES auto_manager_approval_request(id) ON DELETE RESTRICT,
        actor_id uuid NOT NULL REFERENCES app_user(id) ON DELETE RESTRICT,
        action text NOT NULL CHECK (action IN ('PROPOSED','APPROVED')),
        created_at timestamptz NOT NULL DEFAULT now()
      )
    """)
    op.execute("""
      CREATE UNIQUE INDEX ux_auto_manager_approval_audit_action
      ON auto_manager_approval_audit(request_id, action)
    """)
    op.execute("""
      CREATE TRIGGER auto_manager_approval_audit_immutable
      BEFORE UPDATE OR DELETE ON auto_manager_approval_audit
      FOR EACH ROW EXECUTE FUNCTION reject_audit_mutation()
    """)


def downgrade():
    # Только disposable synthetic PG17. Данные об одобрениях удалять нельзя.
    op.execute("DROP TABLE auto_manager_approval_audit")
    op.execute("DROP TABLE auto_manager_approval_request")
    op.execute("DROP FUNCTION guard_auto_manager_approval_request()")
