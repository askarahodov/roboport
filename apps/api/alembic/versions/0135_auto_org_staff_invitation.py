"""Одноразовые приглашения сотрудников в организацию.

Revision ID: 0135_auto_org_staff_invitation
Revises: 0134_auto_workshop_admin

Только закрытый synthetic/dev-контур. Приглашение адресовано уже
существующей активной учётной записи; публичная регистрация и email delivery
не включаются до независимого identity/legal gate #119.
"""
from alembic import op

revision = "0135_auto_org_staff_invitation"
down_revision = "0134_auto_workshop_admin"
branch_labels = None
depends_on = None


def upgrade():
    # CASHIER is already an advertised automotive staff role in the service/UI.
    # Keep the database authority constraint aligned with that contract.
    op.execute("""
      ALTER TABLE auto_organization_role_grant
      DROP CONSTRAINT auto_organization_role_grant_role_code_check
    """)
    op.execute("""
      ALTER TABLE auto_organization_role_grant
      ADD CONSTRAINT auto_organization_role_grant_role_code_check
      CHECK (role_code IN (
        'ADMIN','MANAGER','INTAKE','MECHANIC','WAREHOUSE','CASHIER'
      ))
    """)

    op.execute("""
      CREATE TABLE auto_org_staff_invitation (
        id uuid PRIMARY KEY,
        organization_id uuid NOT NULL REFERENCES auto_organization(id),
        location_id uuid,
        target_user_id uuid NOT NULL REFERENCES app_user(id),
        target_login text NOT NULL
          CHECK (target_login=lower(trim(target_login))
                 AND length(target_login) BETWEEN 3 AND 128),
        role_code text NOT NULL CHECK (role_code IN (
          'MANAGER','INTAKE','MECHANIC','WAREHOUSE','CASHIER'
        )),
        token_hash char(64) NOT NULL UNIQUE
          CHECK (token_hash ~ '^[a-f0-9]{64}$'),
        created_by uuid NOT NULL REFERENCES app_user(id),
        status text NOT NULL DEFAULT 'PENDING'
          CHECK (status IN ('PENDING','ACCEPTED','REVOKED','EXPIRED')),
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        expires_at timestamptz NOT NULL,
        accepted_by uuid REFERENCES app_user(id),
        accepted_grant_id uuid REFERENCES auto_organization_role_grant(id),
        accepted_at timestamptz,
        revoked_at timestamptz,
        expired_at timestamptz,
        CHECK (expires_at > created_at
               AND expires_at <= created_at + interval '30 days'),
        CHECK (
          (status='PENDING' AND accepted_by IS NULL AND accepted_grant_id IS NULL
            AND accepted_at IS NULL AND revoked_at IS NULL AND expired_at IS NULL)
          OR
          (status='ACCEPTED' AND accepted_by=target_user_id
            AND accepted_grant_id IS NOT NULL AND accepted_at IS NOT NULL
            AND revoked_at IS NULL AND expired_at IS NULL)
          OR
          (status='REVOKED' AND accepted_by IS NULL AND accepted_grant_id IS NULL
            AND accepted_at IS NULL AND revoked_at IS NOT NULL AND expired_at IS NULL)
          OR
          (status='EXPIRED' AND accepted_by IS NULL AND accepted_grant_id IS NULL
            AND accepted_at IS NULL AND revoked_at IS NULL AND expired_at IS NOT NULL)
        ),
      )
    """)
    op.execute("""
      ALTER TABLE auto_org_staff_invitation
      ADD CONSTRAINT fk_auto_org_staff_invitation_location
      FOREIGN KEY (location_id) REFERENCES auto_service_location(id)
    """)
    op.execute("""
      CREATE UNIQUE INDEX uq_auto_org_staff_invitation_pending
      ON auto_org_staff_invitation(
        organization_id,target_user_id,role_code,
        COALESCE(location_id,'00000000-0000-0000-0000-000000000000'::uuid)
      ) WHERE status='PENDING'
    """)
    op.execute("""
      CREATE INDEX ix_auto_org_staff_invitation_org_created
      ON auto_org_staff_invitation(organization_id,created_at DESC,id DESC)
    """)

    op.execute("""
      CREATE FUNCTION guard_auto_org_staff_invitation()
      RETURNS trigger LANGUAGE plpgsql AS $fn$
      BEGIN
        IF TG_OP='DELETE' THEN
          RAISE EXCEPTION 'staff invitation history deletion forbidden';
        END IF;
        IF TG_OP='INSERT' THEN
          IF NEW.status<>'PENDING' OR NEW.created_by=NEW.target_user_id THEN
            RAISE EXCEPTION 'staff invitation must start pending for another account';
          END IF;
          PERFORM 1 FROM auto_organization
            WHERE id=NEW.organization_id AND disabled_at IS NULL FOR SHARE;
          IF NOT FOUND THEN RAISE EXCEPTION 'organization unavailable'; END IF;
          IF NEW.location_id IS NOT NULL THEN
            PERFORM 1 FROM auto_service_location
              WHERE id=NEW.location_id AND organization_id=NEW.organization_id
                AND disabled_at IS NULL FOR SHARE;
            IF NOT FOUND THEN RAISE EXCEPTION 'location organization mismatch'; END IF;
          END IF;
          PERFORM 1 FROM app_user
            WHERE id=NEW.target_user_id AND normalized_login=NEW.target_login
              AND status='ACTIVE' FOR SHARE;
          IF NOT FOUND THEN RAISE EXCEPTION 'invitation target unavailable'; END IF;
          PERFORM 1 FROM app_user WHERE id=NEW.created_by AND status='ACTIVE' FOR SHARE;
          IF NOT FOUND THEN RAISE EXCEPTION 'invitation actor unavailable'; END IF;
          RETURN NEW;
        END IF;

        IF (NEW.id,NEW.organization_id,NEW.location_id,NEW.target_user_id,
            NEW.target_login,NEW.role_code,NEW.token_hash,NEW.created_by,
            NEW.created_at,NEW.expires_at)
           IS DISTINCT FROM
           (OLD.id,OLD.organization_id,OLD.location_id,OLD.target_user_id,
            OLD.target_login,OLD.role_code,OLD.token_hash,OLD.created_by,
            OLD.created_at,OLD.expires_at) THEN
          RAISE EXCEPTION 'staff invitation identity immutable';
        END IF;
        IF OLD.status<>'PENDING' OR NEW.status NOT IN ('ACCEPTED','REVOKED','EXPIRED') THEN
          RAISE EXCEPTION 'staff invitation transition forbidden';
        END IF;
        IF NEW.status='ACCEPTED' THEN
          IF NEW.accepted_by IS DISTINCT FROM OLD.target_user_id
             OR NEW.accepted_grant_id IS NULL OR NEW.accepted_at IS NULL
             OR NEW.accepted_at<OLD.created_at OR NEW.accepted_at>OLD.expires_at
             OR NOT EXISTS (
               SELECT 1 FROM auto_organization_role_grant g
               WHERE g.id=NEW.accepted_grant_id
                 AND g.organization_id=OLD.organization_id
                 AND g.location_id IS NOT DISTINCT FROM OLD.location_id
                 AND g.user_id=OLD.target_user_id
                 AND g.role_code=OLD.role_code AND g.revoked_at IS NULL
             ) THEN
            RAISE EXCEPTION 'staff invitation acceptance mismatch';
          END IF;
        ELSIF NEW.status='REVOKED' THEN
          IF NEW.revoked_at IS NULL THEN
            RAISE EXCEPTION 'staff invitation revocation timestamp required';
          END IF;
        ELSIF NEW.status='EXPIRED' THEN
          IF NEW.expired_at IS NULL OR NEW.expired_at<OLD.expires_at THEN
            RAISE EXCEPTION 'staff invitation expiry timestamp required';
          END IF;
        END IF;
        RETURN NEW;
      END $fn$
    """)
    op.execute("""
      CREATE TRIGGER auto_org_staff_invitation_guard
      BEFORE INSERT OR UPDATE OR DELETE ON auto_org_staff_invitation
      FOR EACH ROW EXECUTE FUNCTION guard_auto_org_staff_invitation()
    """)

    op.execute("""
      CREATE TABLE auto_org_staff_invitation_event (
        id uuid PRIMARY KEY,
        invitation_id uuid NOT NULL REFERENCES auto_org_staff_invitation(id),
        organization_id uuid NOT NULL REFERENCES auto_organization(id),
        actor_id uuid NOT NULL REFERENCES app_user(id),
        action text NOT NULL CHECK (action IN ('CREATED','ACCEPTED','REVOKED','EXPIRED')),
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        UNIQUE(invitation_id,action)
      )
    """)
    op.execute("""
      CREATE INDEX ix_auto_org_staff_invitation_event_org_time
      ON auto_org_staff_invitation_event(organization_id,created_at DESC,id DESC)
    """)
    op.execute("""
      CREATE TRIGGER auto_org_staff_invitation_event_immutable
      BEFORE UPDATE OR DELETE ON auto_org_staff_invitation_event
      FOR EACH ROW EXECUTE FUNCTION reject_audit_mutation()
    """)
    op.execute("""
      CREATE TRIGGER auto_org_staff_invitation_event_no_truncate
      BEFORE TRUNCATE ON auto_org_staff_invitation_event
      FOR EACH STATEMENT EXECUTE FUNCTION reject_audit_mutation()
    """)
    op.execute("REVOKE ALL ON auto_org_staff_invitation FROM PUBLIC")
    op.execute("REVOKE ALL ON auto_org_staff_invitation_event FROM PUBLIC")


def downgrade():
    op.execute("""
      DO $body$
      BEGIN
        IF EXISTS (SELECT 1 FROM auto_organization_role_grant WHERE role_code='CASHIER')
           OR EXISTS (SELECT 1 FROM auto_org_staff_invitation WHERE role_code='CASHIER') THEN
          RAISE EXCEPTION 'CASHIER organization grants prevent downgrade';
        END IF;
      END;
      $body$;
    """)
    op.execute("DROP TABLE auto_org_staff_invitation_event")
    op.execute("DROP TABLE auto_org_staff_invitation")
    op.execute("DROP FUNCTION guard_auto_org_staff_invitation()")
    op.execute("""
      ALTER TABLE auto_organization_role_grant
      DROP CONSTRAINT auto_organization_role_grant_role_code_check
    """)
    op.execute("""
      ALTER TABLE auto_organization_role_grant
      ADD CONSTRAINT auto_organization_role_grant_role_code_check
      CHECK (role_code IN ('ADMIN','MANAGER','INTAKE','MECHANIC','WAREHOUSE'))
    """)
