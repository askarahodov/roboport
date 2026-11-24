"""План следующего обслуживания автомобиля, отдельный от сметы и брони.

Revision ID: 0136_auto_service_recommend
Revises: 0135_auto_org_staff_invitation
"""
from alembic import op
from sqlalchemy import text

revision="0136_auto_service_recommend"
down_revision="0135_auto_org_staff_invitation"
branch_labels=None
depends_on=None


def upgrade():
    op.execute("""
      CREATE TABLE auto_service_recommendation (
        id uuid PRIMARY KEY,
        work_order_id uuid NOT NULL REFERENCES auto_work_order(id),
        service_id uuid NOT NULL REFERENCES auto_service_catalog(id),
        actor_id uuid NOT NULL REFERENCES app_user(id),
        request_key uuid NOT NULL UNIQUE,
        service_code text NOT NULL,
        service_title text NOT NULL,
        reason_code text NOT NULL CHECK(reason_code IN ('PERIODIC','DEFERRED','FOLLOWUP')),
        baseline_km integer NOT NULL CHECK(baseline_km BETWEEN 0 AND 2000000),
        due_date date NOT NULL,
        due_km integer CHECK(due_km BETWEEN 0 AND 2000000),
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        CHECK(due_km IS NULL OR due_km>baseline_km)
      )
    """)
    op.execute("""
      CREATE TABLE auto_service_recommendation_withdrawal (
        id uuid PRIMARY KEY,
        recommendation_id uuid NOT NULL UNIQUE REFERENCES auto_service_recommendation(id),
        actor_id uuid NOT NULL REFERENCES app_user(id),
        request_key uuid NOT NULL UNIQUE,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp()
      )
    """)
    op.execute("CREATE INDEX ix_auto_recommendation_order ON auto_service_recommendation(work_order_id,created_at,id)")
    op.execute("""
      CREATE FUNCTION guard_auto_service_recommendation() RETURNS trigger
      LANGUAGE plpgsql AS $f$
      DECLARE origin record;
      BEGIN
        IF TG_OP<>'INSERT' THEN
          RAISE EXCEPTION 'Рекомендации обслуживания неизменяемы';
        END IF;
        SELECT w.organization_id,w.location_id,i.odometer_km,s.code,s.title
          INTO origin
        FROM auto_work_order w
        JOIN auto_appointment a ON a.id=w.appointment_id
          AND a.organization_id=w.organization_id AND a.location_id=w.location_id
        JOIN auto_vehicle_customer_link p ON p.id=a.customer_link_id
          AND p.vehicle_id=a.vehicle_id AND p.verified_at IS NOT NULL
          AND p.verified_by IS NOT NULL AND p.revoked_at IS NULL
        JOIN auto_vehicle_location_binding binding ON binding.vehicle_id=a.vehicle_id
          AND binding.location_id=w.location_id
        JOIN auto_organization o ON o.id=w.organization_id AND o.disabled_at IS NULL
        JOIN auto_service_location l ON l.id=w.location_id
          AND l.organization_id=o.id AND l.disabled_at IS NULL
        JOIN auto_bay_booking b ON b.id=COALESCE((
          SELECT r.new_booking_id FROM auto_appointment_reschedule r
          WHERE r.appointment_id=a.id ORDER BY r.created_at DESC,r.id DESC LIMIT 1
        ),a.booking_id) AND b.organization_id=w.organization_id
          AND b.location_id=w.location_id AND b.canceled_at IS NULL
        JOIN auto_work_order_inspection i ON i.work_order_id=w.id
        JOIN auto_service_catalog s ON s.id=NEW.service_id
          AND s.organization_id=w.organization_id AND s.location_id=w.location_id
        WHERE w.id=NEW.work_order_id
        FOR UPDATE OF w FOR SHARE OF a,p,binding,o,l,b,i,s;
        IF NOT FOUND OR NEW.baseline_km<>origin.odometer_km
           OR NEW.service_code<>origin.code OR NEW.service_title<>origin.title
           OR NEW.due_date<=(NEW.created_at AT TIME ZONE 'UTC')::date
           OR NEW.due_date>(NEW.created_at AT TIME ZONE 'UTC')::date+730 THEN
          RAISE EXCEPTION 'Некорректное происхождение рекомендации';
        END IF;
        IF EXISTS(SELECT 1 FROM auto_service_recommendation r
           WHERE r.work_order_id=NEW.work_order_id AND r.service_id=NEW.service_id
           AND NOT EXISTS(SELECT 1 FROM auto_service_recommendation_withdrawal x
                          WHERE x.recommendation_id=r.id)) THEN
          RAISE EXCEPTION 'Активная рекомендация этой услуги уже существует';
        END IF;
        IF (SELECT count(*) FROM auto_service_recommendation
            WHERE work_order_id=NEW.work_order_id)>=100 THEN
          RAISE EXCEPTION 'Лимит версий рекомендаций заказа';
        END IF;
        RETURN NEW;
      END $f$
    """)
    op.execute("""
      CREATE TRIGGER auto_service_recommendation_guard
      BEFORE INSERT OR UPDATE OR DELETE ON auto_service_recommendation
      FOR EACH ROW EXECUTE FUNCTION guard_auto_service_recommendation()
    """)
    op.execute("""
      CREATE FUNCTION guard_auto_recommendation_withdrawal() RETURNS trigger
      LANGUAGE plpgsql AS $f$
      BEGIN
        IF TG_OP<>'INSERT' THEN
          RAISE EXCEPTION 'История отзыва рекомендаций неизменяема';
        END IF;
        PERFORM w.id FROM auto_work_order w
          JOIN auto_service_recommendation r ON r.work_order_id=w.id
          WHERE r.id=NEW.recommendation_id FOR UPDATE OF w;
        IF NOT FOUND THEN RAISE EXCEPTION 'Рекомендация недоступна'; END IF;
        RETURN NEW;
      END $f$
    """)
    op.execute("""
      CREATE TRIGGER auto_recommendation_withdrawal_guard
      BEFORE INSERT OR UPDATE OR DELETE ON auto_service_recommendation_withdrawal
      FOR EACH ROW EXECUTE FUNCTION guard_auto_recommendation_withdrawal()
    """)
    op.execute("""
      CREATE TABLE auto_recommendation_visit (
        id uuid PRIMARY KEY,
        recommendation_id uuid NOT NULL REFERENCES auto_service_recommendation(id),
        appointment_id uuid NOT NULL UNIQUE REFERENCES auto_appointment(id),
        booking_id uuid NOT NULL REFERENCES auto_bay_booking(id),
        actor_id uuid NOT NULL REFERENCES app_user(id),
        request_key uuid NOT NULL UNIQUE,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp()
      )
    """)
    op.execute("CREATE INDEX ix_auto_recommendation_visit ON auto_recommendation_visit(recommendation_id,created_at,id)")
    op.execute("""
      CREATE FUNCTION guard_auto_recommendation_visit() RETURNS trigger
      LANGUAGE plpgsql AS $f$
      DECLARE origin record;
      BEGIN
        IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'Связь повторного визита неизменяема'; END IF;
        SELECT w.id,w.organization_id,w.location_id,a.customer_link_id,r.service_id
          INTO origin
          FROM auto_service_recommendation r
          JOIN auto_work_order w ON w.id=r.work_order_id
          JOIN auto_appointment a ON a.id=w.appointment_id
          WHERE r.id=NEW.recommendation_id FOR UPDATE OF w;
        IF NOT FOUND OR EXISTS(
          SELECT 1 FROM auto_service_recommendation_withdrawal x
          WHERE x.recommendation_id=NEW.recommendation_id
        ) OR NOT EXISTS(
          SELECT 1 FROM auto_appointment a JOIN auto_work_order w ON w.appointment_id=a.id
          JOIN auto_bay_booking b ON b.id=a.booking_id
          JOIN auto_work_order_service s ON s.work_order_id=w.id
          WHERE a.id=NEW.appointment_id AND w.id<>origin.id
            AND w.organization_id=origin.organization_id AND w.location_id=origin.location_id
            AND a.organization_id=origin.organization_id AND a.location_id=origin.location_id
            AND a.customer_link_id=origin.customer_link_id
            AND b.id=NEW.booking_id AND b.canceled_at IS NULL
            AND s.service_id=origin.service_id AND s.selection_origin='BOOKING'
        ) THEN RAISE EXCEPTION 'Несогласованное происхождение повторного визита'; END IF;
        IF EXISTS(
          SELECT 1 FROM auto_recommendation_visit v JOIN auto_appointment a ON a.id=v.appointment_id
          JOIN auto_bay_booking b ON b.id=COALESCE((
            SELECT rr.new_booking_id FROM auto_appointment_reschedule rr
            WHERE rr.appointment_id=a.id ORDER BY rr.created_at DESC,rr.id DESC LIMIT 1
          ),a.booking_id)
          WHERE v.recommendation_id=NEW.recommendation_id AND b.canceled_at IS NULL
        ) THEN RAISE EXCEPTION 'У рекомендации уже есть действующая запись'; END IF;
        RETURN NEW;
      END $f$
    """)
    op.execute("""
      CREATE TRIGGER auto_recommendation_visit_guard
      BEFORE INSERT OR UPDATE OR DELETE ON auto_recommendation_visit
      FOR EACH ROW EXECUTE FUNCTION guard_auto_recommendation_visit()
    """)
    op.execute("REVOKE ALL ON auto_service_recommendation,auto_service_recommendation_withdrawal,auto_recommendation_visit FROM PUBLIC")


def downgrade():
    if op.get_bind().execute(text("SELECT 1 FROM auto_service_recommendation LIMIT 1")).first():
        raise RuntimeError("История рекомендаций существует: восстановите изолированную резервную копию")
    op.execute("DROP TABLE auto_recommendation_visit")
    op.execute("DROP FUNCTION guard_auto_recommendation_visit()")
    op.execute("DROP TABLE auto_service_recommendation_withdrawal")
    op.execute("DROP TABLE auto_service_recommendation")
    op.execute("DROP FUNCTION guard_auto_recommendation_withdrawal()")
    op.execute("DROP FUNCTION guard_auto_service_recommendation()")
