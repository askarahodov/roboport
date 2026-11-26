"""Версионированная регистрация автомобиля и неизменяемый аудит (#130)."""
from alembic import op
from sqlalchemy import text

revision = "0148_auto_vehicle_registration"
down_revision = "0147_auto_serial_disposition"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
      ALTER TABLE auto_vehicle ADD COLUMN registration_revision integer NOT NULL DEFAULT 0
        CHECK (registration_revision BETWEEN 0 AND 1000000)
    """)
    op.execute("""
      CREATE TABLE auto_vehicle_registration_event (
        id uuid PRIMARY KEY,
        request_id uuid NOT NULL,
        actor_id uuid NOT NULL REFERENCES app_user(id),
        organization_id uuid NOT NULL REFERENCES auto_organization(id),
        location_id uuid NOT NULL REFERENCES auto_service_location(id),
        vehicle_id uuid NOT NULL REFERENCES auto_vehicle(id),
        link_id uuid NOT NULL REFERENCES auto_vehicle_customer_link(id),
        revision integer NOT NULL CHECK (revision BETWEEN 1 AND 1000000),
        action text NOT NULL CHECK (action IN ('CREATE','EDIT')),
        request_fingerprint bytea NOT NULL CHECK (octet_length(request_fingerprint)=32),
        metadata_digest bytea NOT NULL CHECK (octet_length(metadata_digest)=32),
        transaction_id xid8 NOT NULL DEFAULT pg_current_xact_id(),
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        UNIQUE(actor_id,request_id),
        UNIQUE(vehicle_id,revision),
        CHECK ((revision=1 AND action='CREATE') OR (revision>1 AND action='EDIT'))
      )
    """)
    op.execute("""
      CREATE FUNCTION guard_auto_vehicle_registration_event() RETURNS trigger
      LANGUAGE plpgsql AS $fn$
      DECLARE current_revision integer;
      BEGIN
        SELECT v.registration_revision INTO current_revision
          FROM auto_vehicle v JOIN auto_vehicle_location_binding b ON b.vehicle_id=v.id
          JOIN auto_service_location l ON l.id=b.location_id
          JOIN auto_organization o ON o.id=l.organization_id
          WHERE v.id=NEW.vehicle_id AND l.id=NEW.location_id
            AND o.id=NEW.organization_id AND l.disabled_at IS NULL AND o.disabled_at IS NULL
          FOR UPDATE OF v FOR SHARE OF b,l,o;
        IF NOT FOUND OR current_revision+1<>NEW.revision THEN
          RAISE EXCEPTION 'vehicle registration scope/revision mismatch';
        END IF;
        PERFORM 1 FROM auto_organization_role_grant g
          JOIN app_user u ON u.id=g.user_id AND u.status='ACTIVE'
          WHERE g.user_id=NEW.actor_id AND g.organization_id=NEW.organization_id
            AND g.role_code='INTAKE' AND g.revoked_at IS NULL
            AND (g.location_id=NEW.location_id OR g.location_id IS NULL)
          LIMIT 1 FOR SHARE OF g,u;
        IF NOT FOUND THEN RAISE EXCEPTION 'vehicle registration actor unavailable'; END IF;
        PERFORM 1 FROM auto_vehicle_customer_link l
          WHERE l.id=NEW.link_id AND l.vehicle_id=NEW.vehicle_id
            AND l.revoked_at IS NULL AND l.verified_at IS NULL FOR UPDATE;
        IF NOT FOUND OR EXISTS (
          SELECT 1 FROM auto_vehicle_claim c
            JOIN auto_vehicle_customer_link l ON l.id=c.link_id
            WHERE l.vehicle_id=NEW.vehicle_id
        ) OR EXISTS (
          SELECT 1 FROM auto_vehicle_customer_link l
            WHERE l.vehicle_id=NEW.vehicle_id AND l.id<>NEW.link_id
        ) THEN RAISE EXCEPTION 'vehicle registration history already exists'; END IF;
        IF NEW.action='CREATE' THEN
          IF NOT EXISTS (
            SELECT 1 FROM auto_vehicle_link_intake_audit a
            WHERE a.link_id=NEW.link_id AND a.actor_id=NEW.actor_id
          ) OR EXISTS (
            SELECT 1 FROM auto_vehicle_migration_promotion p WHERE p.vehicle_id=NEW.vehicle_id
          ) THEN RAISE EXCEPTION 'vehicle registration provenance mismatch'; END IF;
        ELSIF NOT EXISTS (
          SELECT 1 FROM auto_vehicle_registration_event e
          WHERE e.vehicle_id=NEW.vehicle_id AND e.revision=1
            AND e.link_id=NEW.link_id AND e.organization_id=NEW.organization_id
            AND e.location_id=NEW.location_id
        ) THEN RAISE EXCEPTION 'vehicle registration provenance missing'; END IF;
        NEW.transaction_id:=pg_current_xact_id();
        NEW.created_at:=clock_timestamp();
        RETURN NEW;
      END $fn$
    """)
    op.execute("""
      CREATE TRIGGER auto_vehicle_registration_event_guard BEFORE INSERT
      ON auto_vehicle_registration_event FOR EACH ROW
      EXECUTE FUNCTION guard_auto_vehicle_registration_event()
    """)
    op.execute("""
      CREATE TRIGGER auto_vehicle_registration_event_immutable BEFORE UPDATE OR DELETE
      ON auto_vehicle_registration_event FOR EACH ROW EXECUTE FUNCTION reject_audit_mutation()
    """)
    op.execute("""
      CREATE TRIGGER auto_vehicle_registration_event_no_truncate BEFORE TRUNCATE
      ON auto_vehicle_registration_event FOR EACH STATEMENT EXECUTE FUNCTION reject_audit_mutation()
    """)
    op.execute("""
      CREATE FUNCTION guard_registered_auto_vehicle() RETURNS trigger
      LANGUAGE plpgsql AS $fn$
      BEGIN
        IF TG_OP='INSERT' THEN
          IF NEW.registration_revision<>0 THEN
            RAISE EXCEPTION 'registration must start without revision';
          END IF;
          RETURN NEW;
        END IF;
        IF OLD.registration_revision=0 AND NEW.registration_revision=0 THEN RETURN NEW; END IF;
        IF NEW.registration_revision<>OLD.registration_revision+1
          OR (NEW.id,NEW.vin,NEW.created_at) IS DISTINCT FROM (OLD.id,OLD.vin,OLD.created_at)
          OR NOT EXISTS (
            SELECT 1 FROM auto_vehicle_registration_event e
            WHERE e.vehicle_id=OLD.id AND e.revision=NEW.registration_revision
              AND e.transaction_id=pg_current_xact_id()
              AND e.metadata_digest=sha256(convert_to(
                jsonb_build_array(NEW.make,NEW.model,NEW.variant,NEW.model_year)::text,'UTF8'))
          ) THEN RAISE EXCEPTION 'registered vehicle requires matching immutable event'; END IF;
        IF OLD.registration_revision=0 AND (NEW.make,NEW.model,NEW.variant,NEW.model_year)
          IS DISTINCT FROM (OLD.make,OLD.model,OLD.variant,OLD.model_year)
          THEN RAISE EXCEPTION 'creation must preserve original vehicle fields'; END IF;
        RETURN NEW;
      END $fn$
    """)
    op.execute("""
      CREATE TRIGGER auto_vehicle_registration_write_guard BEFORE INSERT OR UPDATE ON auto_vehicle
      FOR EACH ROW EXECUTE FUNCTION guard_registered_auto_vehicle()
    """)
    op.execute("""
      CREATE FUNCTION check_auto_vehicle_registration_applied() RETURNS trigger
      LANGUAGE plpgsql AS $fn$
      BEGIN
        IF NOT EXISTS (SELECT 1 FROM auto_vehicle v WHERE v.id=NEW.vehicle_id
                       AND v.registration_revision>=NEW.revision)
        THEN RAISE EXCEPTION 'vehicle registration event not applied'; END IF;
        RETURN NULL;
      END $fn$
    """)
    op.execute("""
      CREATE CONSTRAINT TRIGGER auto_vehicle_registration_applied
      AFTER INSERT ON auto_vehicle_registration_event DEFERRABLE INITIALLY DEFERRED
      FOR EACH ROW EXECUTE FUNCTION check_auto_vehicle_registration_applied()
    """)
    op.execute("""
      CREATE FUNCTION guard_registered_vehicle_verification() RETURNS trigger
      LANGUAGE plpgsql AS $fn$
      BEGIN
        IF OLD.verified_at IS NULL AND NEW.verified_at IS NOT NULL
           AND EXISTS (SELECT 1 FROM auto_vehicle_registration_event e
                       WHERE e.vehicle_id=NEW.vehicle_id) THEN
          IF EXISTS (SELECT 1 FROM auto_vehicle_registration_event e
                     WHERE e.vehicle_id=NEW.vehicle_id AND e.actor_id=NEW.verified_by)
             OR NOT EXISTS (
               SELECT 1 FROM auto_vehicle_claim c WHERE c.link_id=NEW.id
                 AND c.status='VERIFIED' AND c.reviewed_by=NEW.verified_by
             ) THEN RAISE EXCEPTION 'registered vehicle requires independent verification'; END IF;
        END IF;
        RETURN NEW;
      END $fn$
    """)
    op.execute("""
      CREATE TRIGGER auto_registered_vehicle_verification BEFORE UPDATE OF verified_at,verified_by
      ON auto_vehicle_customer_link FOR EACH ROW
      EXECUTE FUNCTION guard_registered_vehicle_verification()
    """)
    # Для новых регистраций не допускаем подмену цепочки issue → redeem → review
    # вставкой готового VERIFIED claim или правкой claimant/reviewer без полномочий.
    # Доказательство самого секрета остаётся в приложении; роль владельца БД
    # и production IAM не входят в этот dev guard.
    op.execute("""
      CREATE FUNCTION guard_registered_vehicle_claim_transition() RETURNS trigger
      LANGUAGE plpgsql AS $fn$
      DECLARE scope record; staff_actor uuid; required_role text;
      BEGIN
        SELECT e.vehicle_id,e.organization_id,e.location_id,l.customer_id,l.revoked_at,l.verified_at
          INTO scope
        FROM auto_vehicle_customer_link l
        JOIN auto_vehicle_registration_event e ON e.vehicle_id=l.vehicle_id AND e.revision=1
        WHERE l.id=NEW.link_id FOR SHARE OF l;
        IF NOT FOUND THEN RETURN NEW; END IF;
        IF scope.revoked_at IS NOT NULL OR scope.verified_at IS NOT NULL THEN
          RAISE EXCEPTION 'registered claim link not pending';
        END IF;
        IF TG_OP='INSERT' THEN
          IF NEW.status<>'PENDING' OR NEW.claimant_id IS NOT NULL
             OR NEW.reviewed_by IS NOT NULL OR NEW.proof_attempts<>0 THEN
            RAISE EXCEPTION 'registered claim must start pending';
          END IF;
          staff_actor:=NEW.issued_by;
          required_role:='INTAKE';
        ELSE
          IF OLD.claimant_id IS NULL AND NEW.claimant_id IS NOT NULL THEN
            IF OLD.status<>'PENDING' OR NEW.status<>'PENDING'
               OR NEW.proof_attempts<>OLD.proof_attempts+1
               OR NOT EXISTS (
                 SELECT 1 FROM account_customer_link acl
                 JOIN app_user u ON u.id=acl.user_id AND u.status='ACTIVE'
                 JOIN user_role ur ON ur.user_id=u.id AND ur.role_code='CLIENT'
                  AND ur.revoked_at IS NULL AND (ur.expires_at IS NULL OR ur.expires_at>now())
                 WHERE acl.user_id=NEW.claimant_id AND acl.customer_id=scope.customer_id
                   AND acl.revoked_at IS NULL FOR SHARE OF acl,u,ur
               ) THEN RAISE EXCEPTION 'registered claim claimant unavailable'; END IF;
          END IF;
          IF OLD.status='PENDING' AND NEW.status<>'PENDING' THEN
            IF OLD.claimant_id IS NULL OR NEW.claimant_id IS DISTINCT FROM OLD.claimant_id
               OR NEW.proof_attempts<>OLD.proof_attempts
               OR NOT EXISTS (
                 SELECT 1 FROM auto_vehicle_claim_audit a WHERE a.claim_id=OLD.id
                  AND a.link_id=OLD.link_id AND a.actor_id=OLD.claimant_id
                  AND a.action='PROOF_ACCEPTED'
               ) OR EXISTS (
                 SELECT 1 FROM auto_vehicle_registration_event e WHERE e.vehicle_id=scope.vehicle_id
                  AND e.actor_id=NEW.reviewed_by
               ) OR NOT EXISTS (
                 SELECT 1 FROM account_customer_link acl
                 JOIN app_user u ON u.id=acl.user_id AND u.status='ACTIVE'
                 JOIN user_role ur ON ur.user_id=u.id AND ur.role_code='CLIENT'
                  AND ur.revoked_at IS NULL AND (ur.expires_at IS NULL OR ur.expires_at>now())
                 WHERE acl.user_id=OLD.claimant_id AND acl.customer_id=scope.customer_id
                   AND acl.revoked_at IS NULL FOR SHARE OF acl,u,ur
               ) THEN RAISE EXCEPTION 'registered claim needs independent reviewed proof'; END IF;
            staff_actor:=NEW.reviewed_by;
            required_role:='MANAGER';
          END IF;
        END IF;
        IF required_role IS NOT NULL AND NOT EXISTS (
          SELECT 1 FROM auto_organization_role_grant g
          JOIN app_user u ON u.id=g.user_id AND u.status='ACTIVE'
          JOIN auto_service_location l ON l.id=scope.location_id
            AND l.organization_id=scope.organization_id AND l.disabled_at IS NULL
          JOIN auto_organization o ON o.id=scope.organization_id AND o.disabled_at IS NULL
          WHERE g.user_id=staff_actor AND g.organization_id=scope.organization_id
            AND g.role_code=required_role AND g.revoked_at IS NULL
            AND (g.location_id=scope.location_id OR g.location_id IS NULL)
          FOR SHARE OF g,u,l,o
        ) THEN RAISE EXCEPTION 'registered claim staff scope denied'; END IF;
        RETURN NEW;
      END $fn$
    """)
    op.execute("""
      CREATE TRIGGER auto_registered_claim_transition BEFORE INSERT OR UPDATE
      ON auto_vehicle_claim FOR EACH ROW
      EXECUTE FUNCTION guard_registered_vehicle_claim_transition()
    """)
    op.execute("""
      CREATE FUNCTION check_registered_vehicle_claim_audit() RETURNS trigger
      LANGUAGE plpgsql AS $fn$
      DECLARE expected_action text; expected_actor uuid;
      BEGIN
        IF NOT EXISTS (
          SELECT 1 FROM auto_vehicle_registration_event e
          JOIN auto_vehicle_customer_link l ON l.vehicle_id=e.vehicle_id
          WHERE l.id=NEW.link_id
        ) THEN RETURN NULL; END IF;
        IF TG_OP='INSERT' THEN
          expected_action:='ISSUED';
          expected_actor:=NEW.issued_by;
        ELSIF OLD.claimant_id IS NULL AND NEW.claimant_id IS NOT NULL THEN
          expected_action:='PROOF_ACCEPTED';
          expected_actor:=NEW.claimant_id;
        ELSIF OLD.status='PENDING' AND NEW.status<>'PENDING' THEN
          expected_action:=NEW.status;
          expected_actor:=NEW.reviewed_by;
          IF NEW.status='VERIFIED' AND NOT EXISTS (
            SELECT 1 FROM auto_vehicle_customer_link l
            WHERE l.id=NEW.link_id AND l.verified_at IS NOT NULL
              AND l.verified_by=NEW.reviewed_by
          ) THEN RAISE EXCEPTION 'registered claim verification must apply to link'; END IF;
        ELSIF NEW.proof_attempts>OLD.proof_attempts THEN
          expected_action:='PROOF_REJECTED';
          -- Аудит неуспешных попыток уже ведёт сервис; эта проверка
          -- касается только действий, способных предоставить владение.
          RETURN NULL;
        ELSE RETURN NULL;
        END IF;
        IF NOT EXISTS (
          SELECT 1 FROM auto_vehicle_claim_audit a WHERE a.link_id=NEW.link_id
            AND a.claim_id=NEW.id AND a.actor_id=expected_actor AND a.action=expected_action
        ) THEN RAISE EXCEPTION 'registered claim transition lacks audit'; END IF;
        RETURN NULL;
      END $fn$
    """)
    op.execute("""
      CREATE CONSTRAINT TRIGGER auto_registered_claim_audit
      AFTER INSERT OR UPDATE ON auto_vehicle_claim DEFERRABLE INITIALLY DEFERRED
      FOR EACH ROW EXECUTE FUNCTION check_registered_vehicle_claim_audit()
    """)
    op.execute("REVOKE ALL ON auto_vehicle_registration_event FROM PUBLIC")
    op.execute("REVOKE ALL ON FUNCTION guard_registered_vehicle_claim_transition(),check_registered_vehicle_claim_audit() FROM PUBLIC")


def downgrade():
    if op.get_bind().execute(text("SELECT 1 FROM auto_vehicle_registration_event LIMIT 1")).first():
        raise RuntimeError("История регистрации существует: требуется согласованный rollback")
    op.execute("DROP TRIGGER auto_registered_claim_audit ON auto_vehicle_claim")
    op.execute("DROP FUNCTION check_registered_vehicle_claim_audit()")
    op.execute("DROP TRIGGER auto_registered_claim_transition ON auto_vehicle_claim")
    op.execute("DROP FUNCTION guard_registered_vehicle_claim_transition()")
    op.execute("DROP TRIGGER auto_registered_vehicle_verification ON auto_vehicle_customer_link")
    op.execute("DROP FUNCTION guard_registered_vehicle_verification()")
    op.execute("DROP TRIGGER auto_vehicle_registration_write_guard ON auto_vehicle")
    op.execute("DROP FUNCTION guard_registered_auto_vehicle()")
    op.execute("DROP TABLE auto_vehicle_registration_event")
    op.execute("DROP FUNCTION guard_auto_vehicle_registration_event()")
    op.execute("DROP FUNCTION check_auto_vehicle_registration_applied()")
    op.execute("ALTER TABLE auto_vehicle DROP COLUMN registration_revision")
