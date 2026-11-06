"""Immutable synthetic estimate question and manager response, no free text or PII.

Revision ID: 0102_auto_estimate_clarification
Revises: 0101_auto_crm_contact_choices
"""
from alembic import op
from sqlalchemy import text

revision="0102_auto_estimate_clarification"
down_revision="0101_auto_crm_contact_choices"
branch_labels=None
depends_on=None


def upgrade():
    op.execute("""CREATE TABLE auto_estimate_clarification_request (
      id uuid PRIMARY KEY,
      revision_id uuid NOT NULL UNIQUE REFERENCES auto_work_order_estimate_revision(id),
      work_order_id uuid NOT NULL REFERENCES auto_work_order(id),
      actor_id uuid NOT NULL REFERENCES app_user(id),
      request_key uuid NOT NULL UNIQUE,
      reason_code text NOT NULL CHECK(reason_code IN ('PRICE','WORK_SCOPE','TIMING','OTHER')),
      created_at timestamptz NOT NULL DEFAULT clock_timestamp()
    )""")
    op.execute("""CREATE TABLE auto_estimate_clarification_reply (
      id uuid PRIMARY KEY,
      request_id uuid NOT NULL UNIQUE REFERENCES auto_estimate_clarification_request(id),
      actor_id uuid NOT NULL REFERENCES app_user(id),
      request_key uuid NOT NULL UNIQUE,
      resolution_code text NOT NULL CHECK(resolution_code IN ('EXPLAINED','NEW_VERSION_REQUIRED')),
      created_at timestamptz NOT NULL DEFAULT clock_timestamp()
    )""")
    op.execute("""CREATE FUNCTION guard_auto_estimate_clarification_request()
      RETURNS trigger LANGUAGE plpgsql AS $f$
      BEGIN
        IF NOT EXISTS (
          SELECT 1 FROM auto_work_order_estimate_revision r
          JOIN auto_work_order w ON w.id=r.work_order_id
          JOIN auto_appointment a ON a.id=w.appointment_id
          JOIN auto_vehicle_customer_link proof ON proof.id=a.customer_link_id
            AND proof.vehicle_id=a.vehicle_id
            AND proof.verified_at IS NOT NULL AND proof.revoked_at IS NULL
          JOIN account_customer_link acl ON acl.customer_id=proof.customer_id
            AND acl.user_id=NEW.actor_id AND acl.revoked_at IS NULL
          JOIN user_role role ON role.user_id=NEW.actor_id
            AND role.role_code='CLIENT' AND role.revoked_at IS NULL
            AND (role.expires_at IS NULL OR role.expires_at>now())
          WHERE r.id=NEW.revision_id AND r.work_order_id=NEW.work_order_id
            AND NOT EXISTS (SELECT 1 FROM auto_work_order_estimate_revision newer
              WHERE newer.work_order_id=r.work_order_id AND newer.version>r.version)
            AND NOT EXISTS (SELECT 1 FROM auto_work_order_estimate_decision d
              WHERE d.revision_id=r.id)
          FOR SHARE OF r,w,a,proof,acl,role
        ) THEN RAISE EXCEPTION 'clarification requires current undecided customer estimate'; END IF;
        RETURN NEW;
      END $f$""")
    op.execute("""CREATE TRIGGER auto_estimate_clarification_request_guard
      BEFORE INSERT ON auto_estimate_clarification_request FOR EACH ROW
      EXECUTE FUNCTION guard_auto_estimate_clarification_request()""")
    op.execute("""CREATE FUNCTION guard_auto_estimate_clarification_reply()
      RETURNS trigger LANGUAGE plpgsql AS $f$
      BEGIN
        IF NOT EXISTS (
          SELECT 1 FROM auto_estimate_clarification_request q
          JOIN auto_work_order_estimate_revision r ON r.id=q.revision_id
          JOIN auto_work_order w ON w.id=r.work_order_id
          JOIN auto_organization_role_grant g ON g.organization_id=w.organization_id
            AND (g.location_id IS NULL OR g.location_id=w.location_id)
            AND g.user_id=NEW.actor_id AND g.role_code='MANAGER'
            AND g.revoked_at IS NULL
          WHERE q.id=NEW.request_id AND q.work_order_id=w.id
            AND NOT EXISTS (SELECT 1 FROM auto_work_order_estimate_decision d
              WHERE d.revision_id=r.id)
          FOR SHARE OF q,r,w,g
        ) THEN RAISE EXCEPTION 'clarification reply scope unavailable'; END IF;
        RETURN NEW;
      END $f$""")
    op.execute("""CREATE TRIGGER auto_estimate_clarification_reply_guard
      BEFORE INSERT ON auto_estimate_clarification_reply FOR EACH ROW
      EXECUTE FUNCTION guard_auto_estimate_clarification_reply()""")
    op.execute("""CREATE FUNCTION guard_auto_estimate_clarification_decision()
      RETURNS trigger LANGUAGE plpgsql AS $f$
      BEGIN
        IF EXISTS (
          SELECT 1 FROM auto_estimate_clarification_request q
          LEFT JOIN auto_estimate_clarification_reply answer ON answer.request_id=q.id
          WHERE q.revision_id=NEW.revision_id
            AND (answer.id IS NULL OR answer.resolution_code='NEW_VERSION_REQUIRED')
        ) THEN RAISE EXCEPTION 'clarification unresolved for estimate decision'; END IF;
        RETURN NEW;
      END $f$""")
    op.execute("""CREATE TRIGGER auto_estimate_clarification_decision_guard
      BEFORE INSERT ON auto_work_order_estimate_decision FOR EACH ROW
      EXECUTE FUNCTION guard_auto_estimate_clarification_decision()""")
    op.execute("""CREATE FUNCTION guard_auto_estimate_clarification_reprice()
      RETURNS trigger LANGUAGE plpgsql AS $f$
      BEGIN
        IF EXISTS (
          SELECT 1 FROM auto_work_order_estimate_revision prior
          JOIN auto_estimate_clarification_request q ON q.revision_id=prior.id
          LEFT JOIN auto_estimate_clarification_reply answer ON answer.request_id=q.id
          WHERE prior.work_order_id=NEW.work_order_id AND answer.id IS NULL
        ) THEN RAISE EXCEPTION 'clarification awaiting manager response'; END IF;
        RETURN NEW;
      END $f$""")
    op.execute("""CREATE TRIGGER auto_estimate_clarification_reprice_guard
      BEFORE INSERT ON auto_work_order_estimate_revision FOR EACH ROW
      EXECUTE FUNCTION guard_auto_estimate_clarification_reprice()""")
    for table in ("auto_estimate_clarification_request","auto_estimate_clarification_reply"):
        op.execute(f"""CREATE TRIGGER {table}_immutable
          BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW
          EXECUTE FUNCTION immutable_auto_stock_event()""")
        op.execute(f"REVOKE ALL ON {table} FROM PUBLIC")


def downgrade():
    if op.get_bind().execute(text("SELECT 1 FROM auto_estimate_clarification_request LIMIT 1")).first():
        raise RuntimeError("clarification audit exists: restore isolated synthetic backup")
    op.execute("DROP TRIGGER auto_estimate_clarification_decision_guard ON auto_work_order_estimate_decision")
    op.execute("DROP TRIGGER auto_estimate_clarification_reprice_guard ON auto_work_order_estimate_revision")
    op.execute("DROP FUNCTION guard_auto_estimate_clarification_decision()")
    op.execute("DROP FUNCTION guard_auto_estimate_clarification_reprice()")
    op.execute("DROP TABLE auto_estimate_clarification_reply")
    op.execute("DROP TABLE auto_estimate_clarification_request")
    op.execute("DROP FUNCTION guard_auto_estimate_clarification_reply()")
    op.execute("DROP FUNCTION guard_auto_estimate_clarification_request()")
