"""Evidence-gated synthetic model and capability catalog.

Revision ID: 0015_catalog
Revises: 0014_estimate_decision
"""
from alembic import op

revision="0015_catalog"
down_revision="0014_estimate_decision"
branch_labels=None
depends_on=None

def upgrade():
    op.execute("""CREATE TABLE catalog_model (
      id uuid PRIMARY KEY,
      brand text NOT NULL CHECK(length(trim(brand))>0),
      model text NOT NULL CHECK(length(trim(model))>0),
      variant text NOT NULL CHECK(length(trim(variant))>0),
      dock text NOT NULL CHECK(length(trim(dock))>0),
      region text NOT NULL CHECK(length(trim(region))>0),
      revision text NOT NULL CHECK(length(trim(revision))>0),
      status text NOT NULL DEFAULT 'CANDIDATE'
        CHECK(status IN ('CANDIDATE','SUPPORTED','UNSUPPORTED')),
      UNIQUE(brand,model,variant,dock,region,revision)
    )""")
    op.execute("""CREATE TABLE service_capability (
      id uuid PRIMARY KEY,
      model_id uuid NOT NULL REFERENCES catalog_model(id),
      capability text NOT NULL CHECK(length(trim(capability))>0),
      status text NOT NULL DEFAULT 'CANDIDATE'
        CHECK(status IN ('CANDIDATE','SUPPORTED','UNSUPPORTED')),
      evidence text NOT NULL CHECK(length(trim(evidence))>0),
      UNIQUE(model_id,capability)
    )""")
    op.execute("""CREATE TABLE capability_approval (
      id uuid PRIMARY KEY,
      capability_id uuid NOT NULL REFERENCES service_capability(id),
      actor_id uuid NOT NULL REFERENCES app_user(id),
      role text NOT NULL CHECK(role IN ('ENGINEER','MANAGER')),
      approved boolean NOT NULL,
      created_at timestamptz NOT NULL DEFAULT now(),
      UNIQUE(capability_id,actor_id)
    )""")
    op.execute("""CREATE FUNCTION guard_capability_reviewer() RETURNS trigger
      LANGUAGE plpgsql AS $body$
      BEGIN
        IF NOT EXISTS(
          SELECT 1 FROM app_user u JOIN user_role r ON r.user_id=u.id
          WHERE u.id=NEW.actor_id AND u.status='ACTIVE'
          AND r.role_code=NEW.role AND r.revoked_at IS NULL
          AND (r.expires_at IS NULL OR r.expires_at>now())
        ) THEN
          RAISE EXCEPTION 'reviewer has no live role';
        END IF;
        IF EXISTS(
          SELECT 1 FROM capability_approval a WHERE a.capability_id=NEW.capability_id
            AND a.actor_id=NEW.actor_id
        ) THEN
          RAISE EXCEPTION 'duplicate reviewer';
        END IF;
        RETURN NEW;
      END $body$""")
    op.execute("""CREATE TRIGGER capability_reviewer_guard BEFORE INSERT ON capability_approval
      FOR EACH ROW EXECUTE FUNCTION guard_capability_reviewer()""")
    op.execute("""CREATE TABLE part_compatibility (
      id uuid PRIMARY KEY,
      model_id uuid NOT NULL REFERENCES catalog_model(id),
      part_number text NOT NULL CHECK(length(trim(part_number))>0),
      evidence text NOT NULL CHECK(length(trim(evidence))>0),
      status text NOT NULL DEFAULT 'CANDIDATE'
        CHECK(status IN ('CANDIDATE','SUPPORTED','UNSUPPORTED')),
      revoked_at timestamptz,
      UNIQUE(model_id,part_number)
    )""")
    op.execute("""CREATE TABLE part_compatibility_approval (
      id uuid PRIMARY KEY,
      compatibility_id uuid NOT NULL REFERENCES part_compatibility(id),
      actor_id uuid NOT NULL REFERENCES app_user(id),
      role text NOT NULL CHECK(role IN ('ENGINEER','MANAGER')),
      approved boolean NOT NULL,
      created_at timestamptz NOT NULL DEFAULT now(),
      UNIQUE(compatibility_id,actor_id)
    )""")
    op.execute("""CREATE FUNCTION guard_part_reviewer() RETURNS trigger
      LANGUAGE plpgsql AS $body$
      BEGIN
        IF NOT EXISTS(
          SELECT 1 FROM app_user u JOIN user_role r ON r.user_id=u.id
          WHERE u.id=NEW.actor_id AND u.status='ACTIVE'
            AND r.role_code=NEW.role AND r.revoked_at IS NULL
            AND (r.expires_at IS NULL OR r.expires_at>now())
        ) THEN RAISE EXCEPTION 'part reviewer has no active role'; END IF;
        RETURN NEW;
      END $body$""")
    op.execute("""CREATE TRIGGER part_reviewer_guard BEFORE INSERT ON part_compatibility_approval
      FOR EACH ROW EXECUTE FUNCTION guard_part_reviewer()""")
    op.execute("""CREATE FUNCTION guard_part_promotion() RETURNS trigger
      LANGUAGE plpgsql AS $body$
      BEGIN
        IF NEW.status='SUPPORTED' AND OLD.status IS DISTINCT FROM NEW.status THEN
          IF NEW.revoked_at IS NOT NULL OR NOT EXISTS(
            SELECT 1 FROM catalog_model m WHERE m.id=NEW.model_id AND m.status='SUPPORTED'
          ) OR NOT EXISTS(
            SELECT 1 FROM part_compatibility_approval a
            JOIN part_compatibility_approval b ON a.compatibility_id=b.compatibility_id
             AND b.actor_id<>a.actor_id
            WHERE a.compatibility_id=NEW.id AND a.role='ENGINEER' AND a.approved
             AND b.role='MANAGER' AND b.approved
          ) THEN RAISE EXCEPTION 'two independent part approvals required'; END IF;
        END IF;
        RETURN NEW;
      END $body$""")
    op.execute("""CREATE TRIGGER part_promotion_guard BEFORE UPDATE ON part_compatibility
      FOR EACH ROW EXECUTE FUNCTION guard_part_promotion()""")
    op.execute("""CREATE TABLE catalog_review_receipt (
      actor_id uuid NOT NULL REFERENCES app_user(id),
      request_key text NOT NULL CHECK(length(request_key) BETWEEN 1 AND 128),
      fingerprint text NOT NULL CHECK(length(fingerprint)=64),
      response jsonb NOT NULL,
      created_at timestamptz NOT NULL DEFAULT now(),
      PRIMARY KEY(actor_id,request_key)
    )""")
    op.execute("""CREATE TABLE catalog_audit (
      id uuid PRIMARY KEY,
      capability_id uuid NOT NULL REFERENCES service_capability(id),
      actor_id uuid NOT NULL REFERENCES app_user(id),
      action text NOT NULL,
      created_at timestamptz NOT NULL DEFAULT now()
    )""")
    op.execute("""CREATE FUNCTION prevent_catalog_audit_mutation() RETURNS trigger
      LANGUAGE plpgsql AS $body$ BEGIN
        RAISE EXCEPTION 'catalog audit immutable';
      END $body$""")
    for table in ('capability_approval','part_compatibility_approval','catalog_audit','catalog_review_receipt'):
        op.execute(f"""CREATE TRIGGER {table}_append_only
          BEFORE UPDATE OR DELETE ON {table}
          FOR EACH ROW EXECUTE FUNCTION prevent_catalog_audit_mutation()""")
    op.execute("""CREATE FUNCTION guard_catalog_evidence_mutation() RETURNS trigger
      LANGUAGE plpgsql AS $body$
      BEGIN
        IF TG_TABLE_NAME='service_capability' THEN
          IF ROW(NEW.model_id,NEW.capability,NEW.evidence) IS DISTINCT FROM
             ROW(OLD.model_id,OLD.capability,OLD.evidence)
          THEN RAISE EXCEPTION 'capability identity and evidence immutable'; END IF;
        ELSIF TG_TABLE_NAME='part_compatibility' THEN
          IF ROW(NEW.model_id,NEW.part_number,NEW.evidence) IS DISTINCT FROM
             ROW(OLD.model_id,OLD.part_number,OLD.evidence)
          THEN RAISE EXCEPTION 'part identity and evidence immutable'; END IF;
        END IF;
        IF OLD.status='UNSUPPORTED' AND NEW.status<>OLD.status THEN
          RAISE EXCEPTION 'revoked compatibility cannot reactivate';
        END IF;
        IF OLD.status='SUPPORTED' AND NEW.status='CANDIDATE' THEN
          RAISE EXCEPTION 'approved compatibility cannot return to candidate';
        END IF;
        RETURN NEW;
      END $body$""")
    for table in ('service_capability','part_compatibility'):
        op.execute(f"""CREATE TRIGGER {table}_evidence_immutable BEFORE UPDATE ON {table}
          FOR EACH ROW EXECUTE FUNCTION guard_catalog_evidence_mutation()""")
    op.execute("""CREATE FUNCTION validate_capability_promotion() RETURNS trigger
      LANGUAGE plpgsql AS $body$
      BEGIN
        IF NEW.status='SUPPORTED' AND OLD.status IS DISTINCT FROM NEW.status THEN
          IF NOT EXISTS(
            SELECT 1 FROM catalog_model m WHERE m.id=NEW.model_id AND m.status='SUPPORTED'
          ) OR NOT EXISTS(
            SELECT 1 FROM capability_approval a WHERE a.capability_id=NEW.id AND
              a.role='ENGINEER' AND a.approved
          ) OR NOT EXISTS(
            SELECT 1 FROM capability_approval a WHERE a.capability_id=NEW.id AND
              a.role='MANAGER' AND a.approved
              AND a.actor_id <> (SELECT actor_id FROM capability_approval
                  WHERE capability_id=NEW.id AND role='ENGINEER' AND approved
                  ORDER BY created_at LIMIT 1)
          ) THEN
            RAISE EXCEPTION 'independent capability approvals required';
          END IF;
        END IF;
        RETURN NEW;
      END $body$""")
    op.execute("""CREATE TRIGGER capability_promotion_guard BEFORE UPDATE ON service_capability
      FOR EACH ROW EXECUTE FUNCTION validate_capability_promotion()""")

def downgrade():
    op.execute("DROP TABLE catalog_review_receipt")
    op.execute("DROP TABLE catalog_audit")
    op.execute("DROP TABLE part_compatibility_approval")
    op.execute("DROP TABLE part_compatibility")
    op.execute("DROP TABLE capability_approval")
    op.execute("DROP TABLE service_capability")
    op.execute("DROP TABLE catalog_model")
    op.execute("DROP FUNCTION prevent_catalog_audit_mutation()")
    op.execute("DROP FUNCTION guard_capability_reviewer()")
    op.execute("DROP FUNCTION guard_part_reviewer()")
    op.execute("DROP FUNCTION guard_part_promotion()")
    op.execute("DROP FUNCTION guard_catalog_evidence_mutation()")
    op.execute("DROP FUNCTION validate_capability_promotion()")
