"""Append-only restrictive staff document ACL for synthetic Auto Digital.

Revision ID: 0119_auto_document_acl
Revises: 0118_auto_document_lifecycle
Policy cannot grant access outside existing staff roles. A personal rule can
only narrow, never override a DENY in a role template.
"""
from alembic import op

revision = "0119_auto_document_acl"
down_revision = "0118_auto_document_lifecycle"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
      CREATE TABLE auto_document_access_policy (
        id uuid PRIMARY KEY,
        request_key uuid NOT NULL UNIQUE,
        organization_id uuid NOT NULL REFERENCES auto_organization(id),
        location_id uuid REFERENCES auto_service_location(id),
        role_code text NOT NULL CHECK (
          role_code IN ('MANAGER','INTAKE','MECHANIC','WAREHOUSE')),
        user_id uuid REFERENCES app_user(id),
        document_kind text NOT NULL CHECK (
          document_kind IN ('INTAKE_ACT','ESTIMATE','HANDOVER','SUPPLIER_ORDER')),
        capability text NOT NULL CHECK (
          capability IN ('READ','CREATE','EDIT','WRITE','CLOSE','PRICE_SALES',
                         'PRICE_PURCHASE','DISCOUNT')),
        allowed boolean NOT NULL,
        not_before timestamptz,
        actor_id uuid NOT NULL REFERENCES app_user(id),
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        CHECK (not_before IS NULL OR capability='READ'),
        CHECK (not_before IS NULL OR allowed)
      )
    """)
    op.execute("""
      CREATE INDEX auto_doc_acl_lookup ON auto_document_access_policy
        (organization_id,role_code,document_kind,capability,created_at DESC,id DESC)
    """)
    op.execute("""
      CREATE FUNCTION guard_auto_document_policy()
      RETURNS trigger LANGUAGE plpgsql AS $f$
      DECLARE operator_id uuid;
      BEGIN
        BEGIN
          operator_id:=NULLIF(current_setting('roboport.policy_actor',true),'')::uuid;
        EXCEPTION WHEN invalid_text_representation THEN
          RAISE EXCEPTION 'document policy actor missing';
        END;
        IF operator_id IS NULL OR operator_id IS DISTINCT FROM NEW.actor_id
           OR NOT EXISTS (
             SELECT 1 FROM auto_organization o
             JOIN auto_organization_role_grant g
               ON g.organization_id=o.id AND g.user_id=operator_id
               AND g.role_code='ADMIN' AND g.location_id IS NULL
               AND g.revoked_at IS NULL
             WHERE o.id=NEW.organization_id AND o.disabled_at IS NULL
             FOR SHARE OF o,g
           )
        THEN RAISE EXCEPTION 'document policy ADMIN required'; END IF;
        IF NEW.location_id IS NOT NULL AND NOT EXISTS (
          SELECT 1 FROM auto_service_location l
          WHERE l.id=NEW.location_id AND l.organization_id=NEW.organization_id
            AND l.disabled_at IS NULL FOR SHARE
        ) THEN RAISE EXCEPTION 'document policy branch mismatch'; END IF;
        IF NEW.user_id IS NOT NULL AND NOT EXISTS (
          SELECT 1 FROM auto_organization_role_grant g
          WHERE g.organization_id=NEW.organization_id
            AND g.user_id=NEW.user_id AND g.role_code=NEW.role_code
            AND g.revoked_at IS NULL
            AND (NEW.location_id IS NULL AND g.location_id IS NULL
              OR NEW.location_id IS NOT NULL AND
                 (g.location_id IS NULL OR g.location_id=NEW.location_id))
          FOR SHARE
        ) THEN RAISE EXCEPTION 'document policy target grant missing'; END IF;
        RETURN NEW;
      END $f$
    """)
    op.execute("""
      CREATE TRIGGER auto_document_policy_guard BEFORE INSERT
      ON auto_document_access_policy FOR EACH ROW
      EXECUTE FUNCTION guard_auto_document_policy()
    """)
    op.execute("""
      CREATE TRIGGER auto_document_policy_immutable
      BEFORE UPDATE OR DELETE ON auto_document_access_policy FOR EACH ROW
      EXECUTE FUNCTION immutable_auto_stock_event()
    """)
    op.execute("REVOKE ALL ON auto_document_access_policy FROM PUBLIC")


def downgrade():
    # Only disposable synthetic PostgreSQL. History must be retained otherwise.
    op.execute("DROP TABLE auto_document_access_policy")
    op.execute("DROP FUNCTION guard_auto_document_policy()")
