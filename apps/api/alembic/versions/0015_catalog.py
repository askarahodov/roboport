"""Synthetic exact catalog and two-person capability/compatibility review.

Revision ID: 0015_catalog
Revises: 0014_estimate_decision
"""
from alembic import op

revision="0015_catalog"
down_revision="0014_estimate_decision"
branch_labels=None
depends_on=None


def upgrade():
    op.execute("""CREATE TABLE catalog_brand (
      id uuid PRIMARY KEY, name text NOT NULL UNIQUE CHECK(length(trim(name))>0)
    )""")
    op.execute("""CREATE TABLE catalog_model (
      id uuid PRIMARY KEY,
      brand_id uuid NOT NULL REFERENCES catalog_brand(id),
      name text NOT NULL CHECK(length(trim(name))>0),
      category text NOT NULL CHECK(category IN
        ('vacuum','window_cleaner','lawn_mower','pool_cleaner','companion',
         'quadruped','humanoid','other')),
      UNIQUE(brand_id,name)
    )""")
    op.execute("""CREATE TABLE catalog_dock (
      id uuid PRIMARY KEY,
      dock_sku text NOT NULL,
      region_code text NOT NULL,
      hardware_revision text NOT NULL,
      UNIQUE(dock_sku,region_code,hardware_revision),
      CHECK(length(trim(dock_sku))>0 AND length(trim(region_code))>0
        AND length(trim(hardware_revision))>0)
    )""")
    op.execute("""CREATE TABLE catalog_variant (
      id uuid PRIMARY KEY,
      model_id uuid NOT NULL REFERENCES catalog_model(id),
      manufacturer_sku text NOT NULL,
      region_code text NOT NULL,
      hardware_revision text NOT NULL,
      dock_id uuid NOT NULL REFERENCES catalog_dock(id),
      UNIQUE(model_id,manufacturer_sku,region_code,hardware_revision,dock_id),
      CHECK(length(trim(manufacturer_sku))>0 AND length(trim(region_code))>0
        AND length(trim(hardware_revision))>0)
    )""")
    op.execute("""CREATE TABLE catalog_grant (
      id uuid PRIMARY KEY,
      user_id uuid NOT NULL REFERENCES app_user(id),
      model_id uuid NOT NULL REFERENCES catalog_model(id),
      action text NOT NULL CHECK(action IN ('capability.review','compatibility.review','catalog.read')),
      revoked_at timestamptz,
      expires_at timestamptz
    )""")
    op.execute("""CREATE FUNCTION guard_catalog_grant() RETURNS trigger LANGUAGE plpgsql AS $body$
      BEGIN
        IF (NEW.id,NEW.user_id,NEW.model_id,NEW.action,NEW.expires_at) IS DISTINCT FROM
           (OLD.id,OLD.user_id,OLD.model_id,OLD.action,OLD.expires_at) THEN
          RAISE EXCEPTION 'catalog grant identity immutable';
        END IF;
        IF OLD.revoked_at IS NOT NULL AND NEW.revoked_at IS DISTINCT FROM OLD.revoked_at THEN
          RAISE EXCEPTION 'catalog grant revocation irreversible';
        END IF;
        RETURN NEW;
      END $body$""")
    op.execute("""CREATE TRIGGER catalog_grant_guard BEFORE UPDATE ON catalog_grant
      FOR EACH ROW EXECUTE FUNCTION guard_catalog_grant()""")
    op.execute("""CREATE FUNCTION guard_variant_identity() RETURNS trigger LANGUAGE plpgsql AS $body$
      DECLARE dock_region text;
      BEGIN
        IF TG_OP='DELETE' THEN RAISE EXCEPTION 'variant identity deletion forbidden'; END IF;
        IF TG_OP='UPDATE' AND ROW(NEW.id,NEW.model_id,NEW.manufacturer_sku,
              NEW.region_code,NEW.hardware_revision,NEW.dock_id) IS DISTINCT FROM
             ROW(OLD.id,OLD.model_id,OLD.manufacturer_sku,
              OLD.region_code,OLD.hardware_revision,OLD.dock_id) THEN
          RAISE EXCEPTION 'variant identity immutable';
        END IF;
        SELECT region_code INTO dock_region FROM catalog_dock WHERE id=NEW.dock_id;
        IF dock_region IS DISTINCT FROM NEW.region_code THEN
          RAISE EXCEPTION 'variant and dock region mismatch';
        END IF;
        RETURN NEW;
      END $body$""")
    op.execute("""CREATE TRIGGER catalog_variant_guard BEFORE INSERT OR UPDATE OR DELETE
      ON catalog_variant FOR EACH ROW EXECUTE FUNCTION guard_variant_identity()""")
    op.execute("""CREATE FUNCTION reject_catalog_identity_change() RETURNS trigger LANGUAGE plpgsql
      AS $body$ BEGIN RAISE EXCEPTION 'catalog identity immutable'; END $body$""")
    for tab in ('catalog_dock','catalog_part'):
        if tab == 'catalog_dock':
            op.execute("""CREATE TRIGGER catalog_dock_immutable BEFORE UPDATE OR DELETE
              ON catalog_dock FOR EACH ROW EXECUTE FUNCTION reject_catalog_identity_change()""")
    op.execute("""CREATE TABLE catalog_part (
      id uuid PRIMARY KEY,
      part_number text NOT NULL UNIQUE CHECK(length(trim(part_number))>0),
      name text NOT NULL CHECK(length(trim(name))>0)
    )""")
    op.execute("""CREATE TRIGGER catalog_part_immutable BEFORE UPDATE OR DELETE
      ON catalog_part FOR EACH ROW EXECUTE FUNCTION reject_catalog_identity_change()""")
    op.execute("""CREATE TABLE catalog_evidence (
      id uuid PRIMARY KEY,
      source_uri text NOT NULL CHECK(source_uri LIKE 'https://%' AND length(source_uri)<2048),
      sha256 char(64) NOT NULL CHECK(sha256 ~ '^[0-9a-f]{64}$'),
      created_by uuid NOT NULL REFERENCES app_user(id),
      created_at timestamptz NOT NULL DEFAULT now()
    )""")
    op.execute("""CREATE TABLE service_capability (
      id uuid PRIMARY KEY,
      variant_id uuid NOT NULL REFERENCES catalog_variant(id),
      operation_code text NOT NULL CHECK(length(trim(operation_code))>0),
      evidence_id uuid NOT NULL REFERENCES catalog_evidence(id),
      status text NOT NULL DEFAULT 'candidate'
        CHECK(status IN ('candidate','approved','revoked')),
      created_at timestamptz NOT NULL DEFAULT now(),
      UNIQUE(variant_id,operation_code)
    )""")
    op.execute("""CREATE TABLE catalog_capability_review (
      id uuid PRIMARY KEY,
      capability_id uuid NOT NULL REFERENCES service_capability(id),
      reviewer_id uuid NOT NULL REFERENCES app_user(id),
      role_code text NOT NULL CHECK(role_code IN ('ENGINEER','MANAGER')),
      approved boolean NOT NULL,
      created_at timestamptz NOT NULL DEFAULT now(),
      UNIQUE(capability_id,reviewer_id),
      UNIQUE(capability_id,role_code)
    )""")
    op.execute("""CREATE TABLE part_compatibility (
      id uuid PRIMARY KEY,
      part_id uuid NOT NULL REFERENCES catalog_part(id),
      variant_id uuid NOT NULL REFERENCES catalog_variant(id),
      evidence_id uuid NOT NULL REFERENCES catalog_evidence(id),
      requested_status text NOT NULL DEFAULT 'manufacturer_documented'
        CHECK(requested_status IN ('manufacturer_documented','technician_tested')),
      status text NOT NULL DEFAULT 'unverified'
        CHECK(status IN ('unverified','manufacturer_documented',
                         'technician_tested','incompatible','revoked')),
      created_at timestamptz NOT NULL DEFAULT now(),
      UNIQUE(part_id,variant_id)
    )""")
    op.execute("""CREATE TABLE catalog_compatibility_review (
      id uuid PRIMARY KEY,
      compatibility_id uuid NOT NULL REFERENCES part_compatibility(id),
      reviewer_id uuid NOT NULL REFERENCES app_user(id),
      role_code text NOT NULL CHECK(role_code IN ('ENGINEER','MANAGER')),
      approved boolean NOT NULL,
      created_at timestamptz NOT NULL DEFAULT now(),
      UNIQUE(compatibility_id,reviewer_id),
      UNIQUE(compatibility_id,role_code)
    )""")
    op.execute("""CREATE TABLE catalog_receipt (
      actor_id uuid NOT NULL REFERENCES app_user(id),
      request_key text NOT NULL CHECK(length(request_key) BETWEEN 1 AND 128),
      fingerprint text NOT NULL CHECK(length(fingerprint)=64),
      response jsonb NOT NULL,
      PRIMARY KEY(actor_id,request_key)
    )""")
    op.execute("""CREATE FUNCTION reject_catalog_review_change() RETURNS trigger LANGUAGE plpgsql
      AS $body$ BEGIN RAISE EXCEPTION 'catalog review append only'; END $body$""")
    for tab in ("catalog_capability_review","catalog_compatibility_review","catalog_evidence","catalog_receipt"):
        op.execute(f"""CREATE TRIGGER {tab}_immutable BEFORE UPDATE OR DELETE ON {tab}
                      FOR EACH ROW EXECUTE FUNCTION reject_catalog_review_change()""")
    op.execute("""CREATE FUNCTION guard_catalog_publish() RETURNS trigger LANGUAGE plpgsql AS $body$
      DECLARE approvals integer;
      BEGIN
        IF TG_OP='INSERT' THEN
          IF TG_TABLE_NAME='service_capability' AND NEW.status<>'candidate' THEN
            RAISE EXCEPTION 'new capability must be candidate';
          END IF;
          IF TG_TABLE_NAME='part_compatibility' AND NEW.status<>'unverified' THEN
            RAISE EXCEPTION 'new compatibility must be unverified';
          END IF;
          RETURN NEW;
        END IF;
        IF TG_TABLE_NAME='service_capability' THEN
          IF OLD.status='revoked' AND NEW.status IS DISTINCT FROM OLD.status THEN
            RAISE EXCEPTION 'capability revocation terminal';
          END IF;
          IF (NEW.variant_id,NEW.operation_code,NEW.evidence_id) IS DISTINCT FROM
             (OLD.variant_id,OLD.operation_code,OLD.evidence_id) THEN
            RAISE EXCEPTION 'capability identity immutable';
          END IF;
          IF NEW.status='approved' AND OLD.status<>'approved' THEN
            SELECT count(*) INTO approvals FROM catalog_capability_review
             WHERE capability_id=OLD.id AND approved=true;
            IF approvals<>2 THEN RAISE EXCEPTION 'two independent approvals required'; END IF;
          END IF;
        ELSE
          IF OLD.status='revoked' AND NEW.status IS DISTINCT FROM OLD.status THEN
            RAISE EXCEPTION 'compatibility revocation terminal';
          END IF;
          IF (NEW.part_id,NEW.variant_id,NEW.evidence_id,NEW.requested_status) IS DISTINCT FROM
             (OLD.part_id,OLD.variant_id,OLD.evidence_id,OLD.requested_status) THEN
            RAISE EXCEPTION 'compatibility identity immutable';
          END IF;
          IF NEW.status IN ('manufacturer_documented','technician_tested')
             AND OLD.status NOT IN ('manufacturer_documented','technician_tested') THEN
            SELECT count(*) INTO approvals FROM catalog_compatibility_review
             WHERE compatibility_id=OLD.id AND approved=true;
            IF approvals<>2 THEN RAISE EXCEPTION 'two independent approvals required'; END IF;
          END IF;
        END IF;
        RETURN NEW;
      END $body$""")
    for tab in ("service_capability","part_compatibility"):
        op.execute(f"""CREATE TRIGGER {tab}_guard BEFORE INSERT OR UPDATE ON {tab}
                       FOR EACH ROW EXECUTE FUNCTION guard_catalog_publish()""")


def downgrade():
    for table in ("catalog_receipt","catalog_compatibility_review","part_compatibility",
                  "catalog_capability_review","service_capability","catalog_evidence",
                  "catalog_part","catalog_grant","catalog_variant","catalog_dock",
                  "catalog_model","catalog_brand"):
        op.execute(f"DROP TABLE {table}")
    op.execute("DROP FUNCTION reject_catalog_review_change()")
    op.execute("DROP FUNCTION guard_catalog_publish()")
    op.execute("DROP FUNCTION guard_catalog_grant()")
    op.execute("DROP FUNCTION guard_variant_identity()")
    op.execute("DROP FUNCTION reject_catalog_identity_change()")
