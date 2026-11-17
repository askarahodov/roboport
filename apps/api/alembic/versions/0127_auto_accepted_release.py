"""Освобождение принятого задания только до технического начала.

Revision ID: 0127_auto_accepted_release
Revises: 0126_auto_assignment_withdraw
Сохраняет accepted_at, исходный ACCEPTED event и не удаляет историю.
"""
from alembic import op

revision="0127_auto_accepted_release"
down_revision="0126_auto_assignment_withdraw"
branch_labels=None
depends_on=None


def upgrade():
    # Имя CHECK(accepted_at) генерируется PostgreSQL и может иметь суффикс.
    # Находим его по точной семантике, а не угадываем имя в разных базах.
    op.execute("""DO $$
      DECLARE constraint_name text;
      BEGIN
        SELECT conname INTO constraint_name FROM pg_constraint
        WHERE conrelid='auto_work_order_assignment'::regclass
          AND contype='c'
          AND pg_get_constraintdef(oid) LIKE '%accepted_at%'
          AND pg_get_constraintdef(oid) LIKE '%ACCEPTED%';
        IF constraint_name IS NULL
        THEN RAISE EXCEPTION 'acceptance time guard missing'; END IF;
        EXECUTE format('ALTER TABLE auto_work_order_assignment DROP CONSTRAINT %I',
                       constraint_name);
      END $$""")
    op.execute("""ALTER TABLE auto_work_order_assignment
      ADD CONSTRAINT auto_assignment_accepted_release_time_check
      CHECK ((status IN ('ACCEPTED','RELEASED'))=(accepted_at IS NOT NULL))""")
    op.execute("""ALTER TABLE auto_work_order_assignment
      DROP CONSTRAINT auto_work_order_assignment_status_check""")
    op.execute("""ALTER TABLE auto_work_order_assignment
      ADD CONSTRAINT auto_work_order_assignment_status_check
      CHECK (status IN ('OFFERED','ACCEPTED','DECLINED','WITHDRAWN','RELEASED'))""")
    op.execute("""ALTER TABLE auto_work_order_assignment_event
      DROP CONSTRAINT auto_work_order_assignment_event_action_check""")
    op.execute("""ALTER TABLE auto_work_order_assignment_event
      ADD CONSTRAINT auto_work_order_assignment_event_action_check
      CHECK (action IN ('OFFERED','ACCEPTED','DECLINED','WITHDRAWN','RELEASED'))""")
    op.execute("""CREATE OR REPLACE FUNCTION guard_auto_work_order_assignment()
      RETURNS trigger LANGUAGE plpgsql AS $f$
      DECLARE actor uuid;
      BEGIN
        IF TG_OP='INSERT' THEN
          IF NEW.status<>'OFFERED' OR NEW.status_actor_id IS NOT NULL
             OR NEW.accepted_at IS NOT NULL OR NEW.starts_at<=clock_timestamp()
             OR NEW.ends_at<=NEW.starts_at
             OR NEW.ends_at>NEW.starts_at+interval '24 hours'
          THEN RAISE EXCEPTION 'invalid mechanic assignment offer'; END IF;
          IF NOT EXISTS (
            SELECT 1 FROM auto_work_order w
            JOIN auto_appointment a ON a.id=w.appointment_id
            JOIN LATERAL (
              SELECT COALESCE((SELECT rs.new_booking_id
                FROM auto_appointment_reschedule rs
                WHERE rs.appointment_id=a.id
                ORDER BY rs.created_at DESC,rs.id DESC LIMIT 1),a.booking_id) AS booking_id
            ) current_booking ON TRUE
            JOIN auto_bay_booking b ON b.id=current_booking.booking_id
            JOIN auto_vehicle_customer_link proof
              ON proof.id=a.customer_link_id AND proof.vehicle_id=a.vehicle_id
            JOIN auto_organization_role_grant manager
              ON manager.organization_id=w.organization_id
             AND manager.user_id=NEW.assigned_by AND manager.role_code='MANAGER'
             AND manager.revoked_at IS NULL
             AND (manager.location_id IS NULL OR manager.location_id=w.location_id)
            JOIN auto_organization_role_grant mechanic
              ON mechanic.organization_id=w.organization_id
             AND mechanic.user_id=NEW.mechanic_id AND mechanic.role_code='MECHANIC'
             AND mechanic.revoked_at IS NULL
             AND (mechanic.location_id IS NULL OR mechanic.location_id=w.location_id)
            JOIN app_user u ON u.id=NEW.mechanic_id AND u.status='ACTIVE'
            JOIN auto_staff_shift s ON s.organization_id=w.organization_id
             AND s.location_id=w.location_id AND s.user_id=NEW.mechanic_id
             AND s.canceled_at IS NULL
             AND s.starts_at<=NEW.starts_at AND s.ends_at>=NEW.ends_at
            WHERE w.id=NEW.work_order_id
              AND w.organization_id=NEW.organization_id
              AND w.location_id=NEW.location_id
              AND a.organization_id=w.organization_id
              AND a.location_id=w.location_id
              AND b.organization_id=w.organization_id
              AND b.location_id=w.location_id AND b.canceled_at IS NULL
              AND proof.verified_at IS NOT NULL AND proof.revoked_at IS NULL
          ) THEN RAISE EXCEPTION 'assignment scope, role, proof or shift unavailable'; END IF;
          RETURN NEW;
        END IF;
        IF TG_OP='DELETE' THEN RAISE EXCEPTION 'assignment history immutable'; END IF;
        IF (NEW.id,NEW.work_order_id,NEW.organization_id,NEW.location_id,
            NEW.mechanic_id,NEW.assigned_by,NEW.starts_at,NEW.ends_at,
            NEW.request_key,NEW.created_at)
           IS DISTINCT FROM
           (OLD.id,OLD.work_order_id,OLD.organization_id,OLD.location_id,
            OLD.mechanic_id,OLD.assigned_by,OLD.starts_at,OLD.ends_at,
            OLD.request_key,OLD.created_at)
        THEN RAISE EXCEPTION 'assignment identity immutable'; END IF;
        IF NOT ((OLD.status='OFFERED' AND
             NEW.status IN ('ACCEPTED','DECLINED','WITHDRAWN'))
          OR (OLD.status='ACCEPTED' AND NEW.status='RELEASED'))
        THEN RAISE EXCEPTION 'invalid assignment transition'; END IF;
        IF NEW.status='RELEASED' THEN
          -- Сначала защищённая строка WorkOrder: старт, выдача и QC
          -- используют тот же замок, поэтому снимок нельзя обойти гонкой.
          PERFORM 1 FROM auto_work_order w
            WHERE w.id=NEW.work_order_id
              AND w.organization_id=NEW.organization_id
              AND w.location_id=NEW.location_id FOR UPDATE;
          IF NOT FOUND OR NEW.accepted_at IS DISTINCT FROM OLD.accepted_at
            OR NEW.status_actor_id IS NULL
            OR NOT EXISTS (
              SELECT 1 FROM auto_organization_role_grant g
              JOIN app_user manager_user ON manager_user.id=g.user_id
                AND manager_user.status='ACTIVE'
              JOIN auto_service_location branch ON branch.id=NEW.location_id
                AND branch.organization_id=NEW.organization_id
                AND branch.disabled_at IS NULL
              JOIN auto_organization organization
                ON organization.id=NEW.organization_id
                AND organization.disabled_at IS NULL
              WHERE g.organization_id=NEW.organization_id
                AND g.user_id=NEW.status_actor_id
                AND g.role_code='MANAGER' AND g.revoked_at IS NULL
                AND (g.location_id IS NULL OR g.location_id=NEW.location_id)
              FOR SHARE OF g,manager_user,branch,organization
            )
          THEN RAISE EXCEPTION 'only live scoped manager may release accepted assignment'; END IF;
          IF EXISTS (SELECT 1 FROM auto_work_order_execution
                       WHERE work_order_id=NEW.work_order_id)
            OR EXISTS (SELECT 1 FROM auto_stock_lot_work_order_issue
                       WHERE work_order_id=NEW.work_order_id)
            OR EXISTS (SELECT 1 FROM auto_work_order_stock_event
                       WHERE work_order_id=NEW.work_order_id AND action='ISSUE')
            OR EXISTS (SELECT 1 FROM auto_work_order_qc_event
                       WHERE work_order_id=NEW.work_order_id)
          THEN RAISE EXCEPTION 'accepted assignment already has work evidence'; END IF;
        ELSIF NEW.status='WITHDRAWN' THEN
          -- Менеджер может снять только непринятое предложение; отменённая
          -- смена механика и отозванный proof не должны блокировать очистку.
          IF NEW.status_actor_id IS NULL OR NEW.accepted_at IS NOT NULL
            OR NOT EXISTS (
              SELECT 1 FROM auto_organization_role_grant g
              JOIN app_user manager_user ON manager_user.id=g.user_id
                AND manager_user.status='ACTIVE'
              JOIN auto_service_location branch
                ON branch.id=NEW.location_id
                AND branch.organization_id=NEW.organization_id
                AND branch.disabled_at IS NULL
              JOIN auto_organization org
                ON org.id=NEW.organization_id AND org.disabled_at IS NULL
              WHERE g.organization_id=NEW.organization_id
                AND g.user_id=NEW.status_actor_id
                AND g.role_code='MANAGER' AND g.revoked_at IS NULL
                AND (g.location_id IS NULL OR g.location_id=NEW.location_id)
              FOR SHARE OF g,manager_user,branch,org
            )
          THEN RAISE EXCEPTION 'only live scoped manager may withdraw offer'; END IF;
        ELSIF NEW.status_actor_id IS DISTINCT FROM NEW.mechanic_id THEN
          RAISE EXCEPTION 'only assigned mechanic may respond';
        END IF;
        IF NEW.status='ACCEPTED' AND (
          NEW.accepted_at IS NULL OR NEW.accepted_at>clock_timestamp()
          OR NOT EXISTS (
            SELECT 1 FROM auto_organization_role_grant g
            JOIN app_user u ON u.id=g.user_id AND u.status='ACTIVE'
            JOIN auto_staff_shift s ON s.organization_id=g.organization_id
              AND s.location_id=NEW.location_id AND s.user_id=g.user_id
              AND s.canceled_at IS NULL
              AND s.starts_at<=NEW.starts_at AND s.ends_at>=NEW.ends_at
            WHERE g.organization_id=NEW.organization_id
              AND g.user_id=NEW.mechanic_id AND g.role_code='MECHANIC'
              AND g.revoked_at IS NULL
              AND (g.location_id=NEW.location_id OR g.location_id IS NULL)
          )
        ) THEN RAISE EXCEPTION 'mechanic authority or shift unavailable'; END IF;
        IF NEW.status='DECLINED' AND NEW.accepted_at IS NOT NULL
        THEN RAISE EXCEPTION 'declined assignment cannot be accepted'; END IF;
        RETURN NEW;
      END $f$""")
    op.execute("""CREATE OR REPLACE FUNCTION guard_auto_work_order_assignment_event()
      RETURNS trigger LANGUAGE plpgsql AS $f$
      DECLARE assignment_row auto_work_order_assignment%ROWTYPE;
      BEGIN
        SELECT * INTO assignment_row FROM auto_work_order_assignment
          WHERE id=NEW.assignment_id FOR SHARE;
        IF NOT FOUND OR
          (NEW.action='OFFERED' AND
            (assignment_row.status<>'OFFERED' OR NEW.actor_id<>assignment_row.assigned_by
             OR NEW.request_key<>assignment_row.request_key))
          OR (NEW.action IN ('ACCEPTED','DECLINED') AND
            (assignment_row.status<>NEW.action OR NEW.actor_id<>assignment_row.mechanic_id))
          OR (NEW.action='WITHDRAWN' AND
            (assignment_row.status<>'WITHDRAWN'
             OR NEW.actor_id<>assignment_row.status_actor_id))
          OR (NEW.action='RELEASED' AND
            (assignment_row.status<>'RELEASED'
             OR NEW.actor_id<>assignment_row.status_actor_id))
        THEN RAISE EXCEPTION 'assignment event mismatch'; END IF;
        RETURN NEW;
      END $f$""")
    op.execute("""
      CREATE FUNCTION guard_auto_released_assignment_start()
      RETURNS trigger LANGUAGE plpgsql AS $f$
      DECLARE latest_status text;
      BEGIN
        -- Начало исполнения после снятого ACCEPTED без нового принятия
        -- нельзя даже через SQL, без поручения конкретному механику.
        IF EXISTS(SELECT 1 FROM auto_work_order_assignment
                  WHERE work_order_id=NEW.work_order_id AND status='RELEASED')
        THEN
          SELECT status INTO latest_status FROM auto_work_order_assignment
          WHERE work_order_id=NEW.work_order_id
          ORDER BY created_at DESC,id DESC LIMIT 1 FOR SHARE;
          IF latest_status IS DISTINCT FROM 'ACCEPTED'
          THEN RAISE EXCEPTION 'new accepted mechanic required after release'; END IF;
        END IF;
        RETURN NEW;
      END $f$
    """)
    op.execute("""CREATE TRIGGER zz_auto_released_assignment_start_guard
       BEFORE INSERT ON auto_work_order_execution FOR EACH ROW
       EXECUTE FUNCTION guard_auto_released_assignment_start()""")


def downgrade():
    # Сохранённая история релиза, включая принятие, не подлежит удалению.
    op.execute("""DO $$ BEGIN
      IF EXISTS(SELECT 1 FROM auto_work_order_assignment WHERE status='RELEASED')
         OR EXISTS(SELECT 1 FROM auto_work_order_assignment_event
                   WHERE action='RELEASED')
      THEN RAISE EXCEPTION 'accepted release history prevents unsafe downgrade';
      END IF;
      END $$""")
    op.execute("""DROP TRIGGER zz_auto_released_assignment_start_guard
      ON auto_work_order_execution""")
    op.execute("DROP FUNCTION guard_auto_released_assignment_start()")
    op.execute("""CREATE OR REPLACE FUNCTION guard_auto_work_order_assignment()
      RETURNS trigger LANGUAGE plpgsql AS $f$
      DECLARE actor uuid;
      BEGIN
        IF TG_OP='INSERT' THEN
          IF NEW.status<>'OFFERED' OR NEW.status_actor_id IS NOT NULL
             OR NEW.accepted_at IS NOT NULL OR NEW.starts_at<=clock_timestamp()
             OR NEW.ends_at<=NEW.starts_at
             OR NEW.ends_at>NEW.starts_at+interval '24 hours'
          THEN RAISE EXCEPTION 'invalid mechanic assignment offer'; END IF;
          IF NOT EXISTS (
            SELECT 1 FROM auto_work_order w
            JOIN auto_appointment a ON a.id=w.appointment_id
            JOIN LATERAL (
              SELECT COALESCE((SELECT rs.new_booking_id
                FROM auto_appointment_reschedule rs
                WHERE rs.appointment_id=a.id
                ORDER BY rs.created_at DESC,rs.id DESC LIMIT 1),a.booking_id) AS booking_id
            ) current_booking ON TRUE
            JOIN auto_bay_booking b ON b.id=current_booking.booking_id
            JOIN auto_vehicle_customer_link proof
              ON proof.id=a.customer_link_id AND proof.vehicle_id=a.vehicle_id
            JOIN auto_organization_role_grant manager
              ON manager.organization_id=w.organization_id
             AND manager.user_id=NEW.assigned_by AND manager.role_code='MANAGER'
             AND manager.revoked_at IS NULL
             AND (manager.location_id IS NULL OR manager.location_id=w.location_id)
            JOIN auto_organization_role_grant mechanic
              ON mechanic.organization_id=w.organization_id
             AND mechanic.user_id=NEW.mechanic_id AND mechanic.role_code='MECHANIC'
             AND mechanic.revoked_at IS NULL
             AND (mechanic.location_id IS NULL OR mechanic.location_id=w.location_id)
            JOIN app_user u ON u.id=NEW.mechanic_id AND u.status='ACTIVE'
            JOIN auto_staff_shift s ON s.organization_id=w.organization_id
             AND s.location_id=w.location_id AND s.user_id=NEW.mechanic_id
             AND s.canceled_at IS NULL
             AND s.starts_at<=NEW.starts_at AND s.ends_at>=NEW.ends_at
            WHERE w.id=NEW.work_order_id
              AND w.organization_id=NEW.organization_id
              AND w.location_id=NEW.location_id
              AND a.organization_id=w.organization_id
              AND a.location_id=w.location_id
              AND b.organization_id=w.organization_id
              AND b.location_id=w.location_id AND b.canceled_at IS NULL
              AND proof.verified_at IS NOT NULL AND proof.revoked_at IS NULL
          ) THEN RAISE EXCEPTION 'assignment scope, role, proof or shift unavailable'; END IF;
          RETURN NEW;
        END IF;
        IF TG_OP='DELETE' THEN RAISE EXCEPTION 'assignment history immutable'; END IF;
        IF (NEW.id,NEW.work_order_id,NEW.organization_id,NEW.location_id,
            NEW.mechanic_id,NEW.assigned_by,NEW.starts_at,NEW.ends_at,
            NEW.request_key,NEW.created_at)
           IS DISTINCT FROM
           (OLD.id,OLD.work_order_id,OLD.organization_id,OLD.location_id,
            OLD.mechanic_id,OLD.assigned_by,OLD.starts_at,OLD.ends_at,
            OLD.request_key,OLD.created_at)
        THEN RAISE EXCEPTION 'assignment identity immutable'; END IF;
        IF OLD.status<>'OFFERED' OR NEW.status NOT IN ('ACCEPTED','DECLINED','WITHDRAWN')
        THEN RAISE EXCEPTION 'invalid assignment transition'; END IF;
        IF NEW.status='WITHDRAWN' THEN
          -- Менеджер может снять только непринятое предложение; отменённая
          -- смена механика и отозванный proof не должны блокировать очистку.
          IF NEW.status_actor_id IS NULL OR NEW.accepted_at IS NOT NULL
            OR NOT EXISTS (
              SELECT 1 FROM auto_organization_role_grant g
              JOIN app_user manager_user ON manager_user.id=g.user_id
                AND manager_user.status='ACTIVE'
              JOIN auto_service_location branch
                ON branch.id=NEW.location_id
                AND branch.organization_id=NEW.organization_id
                AND branch.disabled_at IS NULL
              JOIN auto_organization org
                ON org.id=NEW.organization_id AND org.disabled_at IS NULL
              WHERE g.organization_id=NEW.organization_id
                AND g.user_id=NEW.status_actor_id
                AND g.role_code='MANAGER' AND g.revoked_at IS NULL
                AND (g.location_id IS NULL OR g.location_id=NEW.location_id)
              FOR SHARE OF g,manager_user,branch,org
            )
          THEN RAISE EXCEPTION 'only live scoped manager may withdraw offer'; END IF;
        ELSIF NEW.status_actor_id IS DISTINCT FROM NEW.mechanic_id THEN
          RAISE EXCEPTION 'only assigned mechanic may respond';
        END IF;
        IF NEW.status='ACCEPTED' AND (
          NEW.accepted_at IS NULL OR NEW.accepted_at>clock_timestamp()
          OR NOT EXISTS (
            SELECT 1 FROM auto_organization_role_grant g
            JOIN app_user u ON u.id=g.user_id AND u.status='ACTIVE'
            JOIN auto_staff_shift s ON s.organization_id=g.organization_id
              AND s.location_id=NEW.location_id AND s.user_id=g.user_id
              AND s.canceled_at IS NULL
              AND s.starts_at<=NEW.starts_at AND s.ends_at>=NEW.ends_at
            WHERE g.organization_id=NEW.organization_id
              AND g.user_id=NEW.mechanic_id AND g.role_code='MECHANIC'
              AND g.revoked_at IS NULL
              AND (g.location_id=NEW.location_id OR g.location_id IS NULL)
          )
        ) THEN RAISE EXCEPTION 'mechanic authority or shift unavailable'; END IF;
        IF NEW.status='DECLINED' AND NEW.accepted_at IS NOT NULL
        THEN RAISE EXCEPTION 'declined assignment cannot be accepted'; END IF;
        RETURN NEW;
      END $f$""")
    op.execute("""CREATE OR REPLACE FUNCTION guard_auto_work_order_assignment_event()
      RETURNS trigger LANGUAGE plpgsql AS $f$
      DECLARE assignment_row auto_work_order_assignment%ROWTYPE;
      BEGIN
        SELECT * INTO assignment_row FROM auto_work_order_assignment
          WHERE id=NEW.assignment_id FOR SHARE;
        IF NOT FOUND OR
          (NEW.action='OFFERED' AND
            (assignment_row.status<>'OFFERED' OR NEW.actor_id<>assignment_row.assigned_by
             OR NEW.request_key<>assignment_row.request_key))
          OR (NEW.action IN ('ACCEPTED','DECLINED') AND
            (assignment_row.status<>NEW.action OR NEW.actor_id<>assignment_row.mechanic_id))
          OR (NEW.action='WITHDRAWN' AND
            (assignment_row.status<>'WITHDRAWN'
             OR NEW.actor_id<>assignment_row.status_actor_id))
        THEN RAISE EXCEPTION 'assignment event mismatch'; END IF;
        RETURN NEW;
      END $f$""")
    op.execute("""ALTER TABLE auto_work_order_assignment
      DROP CONSTRAINT auto_assignment_accepted_release_time_check""")
    op.execute("""ALTER TABLE auto_work_order_assignment
      ADD CONSTRAINT auto_assignment_accepted_release_time_check
      CHECK ((status='ACCEPTED')=(accepted_at IS NOT NULL))""")
    op.execute("""ALTER TABLE auto_work_order_assignment
      DROP CONSTRAINT auto_work_order_assignment_status_check""")
    op.execute("""ALTER TABLE auto_work_order_assignment
      ADD CONSTRAINT auto_work_order_assignment_status_check
      CHECK (status IN ('OFFERED','ACCEPTED','DECLINED','WITHDRAWN'))""")
    op.execute("""ALTER TABLE auto_work_order_assignment_event
      DROP CONSTRAINT auto_work_order_assignment_event_action_check""")
    op.execute("""ALTER TABLE auto_work_order_assignment_event
      ADD CONSTRAINT auto_work_order_assignment_event_action_check
      CHECK (action IN ('OFFERED','ACCEPTED','DECLINED','WITHDRAWN'))""")
