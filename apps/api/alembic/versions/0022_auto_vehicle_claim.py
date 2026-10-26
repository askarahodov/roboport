"""Проверка автомобильной связи: одноразовое доказательство и независимое решение.

Revision ID: 0022_auto_vehicle_claim
Revises: 0021_auto_vehicle

Изолированная dev-схема: не создаёт публичных прав и не меняет старые заявки.
"""
from alembic import op

revision = "0022_auto_vehicle_claim"
down_revision = "0021_auto_vehicle"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
      CREATE TABLE auto_vehicle_staff_assignment (
        id uuid PRIMARY KEY,
        vehicle_id uuid NOT NULL REFERENCES auto_vehicle(id) ON DELETE RESTRICT,
        user_id uuid NOT NULL REFERENCES app_user(id) ON DELETE RESTRICT,
        role_code text NOT NULL CHECK (role_code IN ('INTAKE','MANAGER')),
        created_at timestamptz NOT NULL DEFAULT now(),
        revoked_at timestamptz CHECK (revoked_at IS NULL OR revoked_at >= created_at)
      )
    """)
    op.execute("""
      CREATE UNIQUE INDEX ux_auto_vehicle_staff_active
      ON auto_vehicle_staff_assignment(vehicle_id,user_id,role_code)
      WHERE revoked_at IS NULL
    """)
    op.execute("""
      CREATE TABLE auto_vehicle_claim (
        id uuid PRIMARY KEY,
        link_id uuid NOT NULL UNIQUE REFERENCES auto_vehicle_customer_link(id) ON DELETE RESTRICT,
        issued_by uuid NOT NULL REFERENCES app_user(id) ON DELETE RESTRICT,
        proof_hash char(64) NOT NULL CHECK (proof_hash ~ '^[0-9a-f]{64}$'),
        proof_expires_at timestamptz NOT NULL,
        proof_attempts integer NOT NULL DEFAULT 0 CHECK (proof_attempts BETWEEN 0 AND 5),
        claimant_id uuid REFERENCES app_user(id) ON DELETE RESTRICT,
        claimed_at timestamptz,
        reviewed_by uuid REFERENCES app_user(id) ON DELETE RESTRICT,
        reviewed_at timestamptz,
        status text NOT NULL DEFAULT 'PENDING'
          CHECK (status IN ('PENDING','VERIFIED','DECLINED')),
        created_at timestamptz NOT NULL DEFAULT now(),
        CHECK ((claimant_id IS NULL AND claimed_at IS NULL)
          OR (claimant_id IS NOT NULL AND claimed_at IS NOT NULL)),
        CHECK ((status='PENDING' AND reviewed_by IS NULL AND reviewed_at IS NULL)
          OR (status<>'PENDING' AND reviewed_by IS NOT NULL AND reviewed_at IS NOT NULL)),
        CHECK (status<>'VERIFIED' OR claimant_id IS NOT NULL),
        CHECK (reviewed_by IS NULL OR reviewed_by <> issued_by),
        CHECK (reviewed_by IS NULL OR reviewed_by <> claimant_id)
      )
    """)
    op.execute("""
      CREATE FUNCTION guard_auto_vehicle_claim() RETURNS trigger LANGUAGE plpgsql AS $fn$
      BEGIN
        IF (NEW.id, NEW.link_id, NEW.issued_by, NEW.proof_hash, NEW.proof_expires_at, NEW.created_at)
            IS DISTINCT FROM
           (OLD.id, OLD.link_id, OLD.issued_by, OLD.proof_hash, OLD.proof_expires_at, OLD.created_at) THEN
          RAISE EXCEPTION 'auto claim identity immutable';
        END IF;
        IF OLD.status <> 'PENDING' AND NEW IS DISTINCT FROM OLD THEN
          RAISE EXCEPTION 'terminal auto claim immutable';
        END IF;
        IF OLD.claimant_id IS NOT NULL AND
            (NEW.claimant_id, NEW.claimed_at) IS DISTINCT FROM
            (OLD.claimant_id, OLD.claimed_at) THEN
          RAISE EXCEPTION 'auto claimant immutable';
        END IF;
        IF NEW.proof_attempts < OLD.proof_attempts THEN
          RAISE EXCEPTION 'auto attempts irreversible';
        END IF;
        RETURN NEW;
      END $fn$
    """)
    op.execute("""
      CREATE TRIGGER auto_vehicle_claim_guard BEFORE UPDATE ON auto_vehicle_claim
      FOR EACH ROW EXECUTE FUNCTION guard_auto_vehicle_claim()
    """)
    op.execute("""
      CREATE TABLE auto_vehicle_claim_audit (
        id uuid PRIMARY KEY,
        link_id uuid NOT NULL REFERENCES auto_vehicle_customer_link(id) ON DELETE RESTRICT,
        claim_id uuid REFERENCES auto_vehicle_claim(id) ON DELETE RESTRICT,
        actor_id uuid NOT NULL REFERENCES app_user(id) ON DELETE RESTRICT,
        action text NOT NULL CHECK (
          action IN ('ISSUED','PROOF_ACCEPTED','PROOF_REJECTED','VERIFIED','DECLINED','REVOKED')
        ),
        created_at timestamptz NOT NULL DEFAULT now()
      )
    """)
    op.execute("""
      CREATE TRIGGER auto_vehicle_claim_audit_immutable
      BEFORE UPDATE OR DELETE ON auto_vehicle_claim_audit
      FOR EACH ROW EXECUTE FUNCTION reject_audit_mutation()
    """)


def downgrade():
    # Применять только к одноразовой тестовой базе; запись в рабочую БД запрещена.
    op.execute("DROP TABLE auto_vehicle_claim_audit")
    op.execute("DROP TABLE auto_vehicle_claim")
    op.execute("DROP FUNCTION guard_auto_vehicle_claim()")
    op.execute("DROP TABLE auto_vehicle_staff_assignment")
