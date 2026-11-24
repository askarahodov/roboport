"""Синтетический опрос после технического закрытия: неизменяемые приглашение и ответ."""
from alembic import op
from sqlalchemy import text

revision="0139_auto_crm_survey"
down_revision="0138_auto_writeoff_document"
branch_labels=None
depends_on=None


def upgrade():
    op.execute("""
      CREATE TABLE auto_crm_survey (
        id uuid PRIMARY KEY,
        lead_id uuid NOT NULL UNIQUE REFERENCES auto_crm_lead(id),
        work_order_id uuid NOT NULL UNIQUE REFERENCES auto_work_order(id),
        actor_id uuid NOT NULL REFERENCES app_user(id),
        request_key uuid NOT NULL UNIQUE,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp()
      );
      CREATE TABLE auto_crm_survey_result (
        id uuid PRIMARY KEY,
        survey_id uuid NOT NULL UNIQUE REFERENCES auto_crm_survey(id),
        actor_id uuid NOT NULL REFERENCES app_user(id),
        request_key uuid NOT NULL UNIQUE,
        action text NOT NULL CHECK(action IN ('ANSWERED','CANCELED')),
        rating integer CHECK(rating BETWEEN 1 AND 5),
        reason_code text CHECK(reason_code IN ('QUALITY','TIMING','COMMUNICATION','NONE','OTHER')),
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        CHECK((action='ANSWERED' AND rating IS NOT NULL AND reason_code IS NOT NULL)
          OR (action='CANCELED' AND rating IS NULL AND reason_code IS NULL))
      );
      CREATE INDEX ix_auto_crm_survey_created ON auto_crm_survey(created_at,id);
    """)
    op.execute("""
      CREATE FUNCTION auto_crm_survey_live_scope(wid uuid,lid uuid)
      RETURNS TABLE(org uuid,loc uuid,customer uuid,owner_id uuid)
      LANGUAGE plpgsql AS $f$
      BEGIN
        RETURN QUERY
        SELECT w.organization_id,w.location_id,p.customer_id,
          COALESCE((SELECT e.assignee_id FROM auto_crm_lead_assignment_event e
            WHERE e.lead_id=lead.id ORDER BY e.created_at DESC,e.id DESC LIMIT 1),lead.owner_id)
        FROM auto_work_order w
        JOIN auto_appointment a ON a.id=w.appointment_id
          AND a.organization_id=w.organization_id AND a.location_id=w.location_id
        JOIN auto_crm_lead lead ON lead.id=lid AND lead.organization_id=w.organization_id
          AND lead.location_id=w.location_id AND lead.customer_link_id=a.customer_link_id
        JOIN auto_vehicle_customer_link p ON p.id=a.customer_link_id AND p.vehicle_id=a.vehicle_id
          AND p.verified_at IS NOT NULL AND p.verified_by IS NOT NULL AND p.revoked_at IS NULL
        JOIN auto_vehicle_location_binding binding ON binding.vehicle_id=a.vehicle_id
          AND binding.location_id=w.location_id
        JOIN auto_organization o ON o.id=w.organization_id AND o.disabled_at IS NULL
        JOIN auto_service_location l ON l.id=w.location_id AND l.organization_id=o.id AND l.disabled_at IS NULL
        JOIN auto_bay_booking b ON b.id=COALESCE((
          SELECT r.new_booking_id FROM auto_appointment_reschedule r WHERE r.appointment_id=a.id
          ORDER BY r.created_at DESC,r.id DESC LIMIT 1),a.booking_id)
          AND b.organization_id=w.organization_id AND b.location_id=w.location_id AND b.canceled_at IS NULL
        WHERE w.id=wid AND EXISTS(SELECT 1 FROM auto_crm_lead_stage s
          WHERE s.lead_id=lid AND s.stage_code='BOOKED' AND s.appointment_id=a.id)
          AND EXISTS(SELECT 1 FROM auto_order_demo_document_event e
            WHERE e.work_order_id=w.id AND e.action='CLOSED_TECHNICAL')
        FOR SHARE OF w,a,p,binding,o,l,b;
      END $f$;
      CREATE FUNCTION guard_auto_crm_survey() RETURNS trigger LANGUAGE plpgsql AS $f$
      DECLARE origin record; invitation record;
      BEGIN
        IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'CRM survey is append-only'; END IF;
        NEW.created_at:=clock_timestamp();
        IF TG_TABLE_NAME='auto_crm_survey' THEN
          PERFORM id FROM auto_crm_lead WHERE id=NEW.lead_id FOR UPDATE;
          PERFORM id FROM auto_work_order WHERE id=NEW.work_order_id FOR UPDATE;
          SELECT * INTO origin FROM auto_crm_survey_live_scope(NEW.work_order_id,NEW.lead_id);
        ELSE
          SELECT * INTO invitation FROM auto_crm_survey WHERE id=NEW.survey_id;
          IF NOT FOUND THEN RAISE EXCEPTION 'Survey unavailable'; END IF;
          PERFORM id FROM auto_crm_lead WHERE id=invitation.lead_id FOR UPDATE;
          PERFORM id FROM auto_work_order WHERE id=invitation.work_order_id FOR UPDATE;
          SELECT * INTO origin FROM auto_crm_survey_live_scope(invitation.work_order_id,invitation.lead_id);
          IF NEW.created_at>=invitation.created_at+interval '30 days' THEN
            RAISE EXCEPTION 'Survey expired';
          END IF;
        END IF;
        IF NOT FOUND THEN RAISE EXCEPTION 'Survey scope unavailable'; END IF;
        IF TG_TABLE_NAME='auto_crm_survey_result' THEN
          IF NEW.action='ANSWERED' THEN
            IF NOT EXISTS(SELECT 1 FROM account_customer_link acl
              JOIN user_role role ON role.user_id=acl.user_id AND role.role_code='CLIENT'
                AND role.revoked_at IS NULL AND (role.expires_at IS NULL OR role.expires_at>now())
              JOIN app_user u ON u.id=acl.user_id AND u.status='ACTIVE'
              WHERE acl.user_id=NEW.actor_id AND acl.customer_id=origin.customer AND acl.revoked_at IS NULL
              FOR SHARE OF acl,role,u) THEN RAISE EXCEPTION 'Survey customer unavailable'; END IF;
            RETURN NEW;
          END IF;
        END IF;
        IF NOT EXISTS(SELECT 1 FROM auto_organization_role_grant g
          JOIN app_user u ON u.id=g.user_id AND u.status='ACTIVE'
          WHERE g.user_id=NEW.actor_id AND g.organization_id=origin.org
          AND (g.location_id IS NULL OR g.location_id=origin.loc) AND g.revoked_at IS NULL
          AND (g.role_code='MANAGER' OR (g.role_code='INTAKE' AND NEW.actor_id=origin.owner_id))
          FOR SHARE OF g,u) THEN RAISE EXCEPTION 'Survey staff unavailable'; END IF;
        RETURN NEW;
      END $f$;
      CREATE TRIGGER auto_crm_survey_guard BEFORE INSERT OR UPDATE OR DELETE ON auto_crm_survey
        FOR EACH ROW EXECUTE FUNCTION guard_auto_crm_survey();
      CREATE TRIGGER auto_crm_survey_result_guard BEFORE INSERT OR UPDATE OR DELETE ON auto_crm_survey_result
        FOR EACH ROW EXECUTE FUNCTION guard_auto_crm_survey();
    """)


def downgrade():
    db=op.get_bind()
    if db.execute(text("SELECT EXISTS(SELECT 1 FROM auto_crm_survey)")).scalar():
        raise RuntimeError("CRM survey evidence requires verified restore")
    op.execute("DROP TABLE auto_crm_survey_result; DROP TABLE auto_crm_survey; DROP FUNCTION guard_auto_crm_survey(); DROP FUNCTION auto_crm_survey_live_scope(uuid,uuid)")
