"""Shift-aware mechanic assignment offers and immutable acceptance evidence.

Revision ID: 0108_auto_work_order_assignment
Revises: 0107_auto_work_pricing
"""
from alembic import op

revision = "0108_auto_work_order_assignment"
down_revision = "0107_auto_work_pricing"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
      CREATE TABLE auto_work_order_assignment (
        id uuid PRIMARY KEY,
        work_order_id uuid NOT NULL REFERENCES auto_work_order(id),
        organization_id uuid NOT NULL REFERENCES auto_organization(id),
        location_id uuid NOT NULL REFERENCES auto_service_location(id),
        mechanic_id uuid NOT NULL REFERENCES app_user(id),
        assigned_by uuid NOT NULL REFERENCES app_user(id),
        starts_at timestamptz NOT NULL,
        ends_at timestamptz NOT NULL,
        status text NOT NULL DEFAULT 'OFFERED'
          CHECK (status IN ('OFFERED','ACCEPTED','DECLINED')),
        status_actor_id uuid REFERENCES app_user(id),
        accepted_at timestamptz,
        request_key uuid NOT NULL UNIQUE,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        CHECK (ends_at > starts_at),
        CHECK (ends_at <= starts_at + interval '24 hours'),
        CHECK ((status='ACCEPTED') = (accepted_at IS NOT NULL))
      )
    """)
    op.execute("""
      CREATE UNIQUE INDEX uq_auto_work_order_active_assignment
        ON auto_work_order_assignment(work_order_id)
        WHERE status IN ('OFFERED','ACCEPTED')
    """)
    op.execute("""
      ALTER TABLE auto_work_order_assignment
        ADD CONSTRAINT ex_auto_mechanic_assignment_overlap
        EXCLUDE USING gist (
          mechanic_id WITH =,
          tstzrange(starts_at,ends_at,'[)') WITH &&
        ) WHERE (status IN ('OFFERED','ACCEPTED'))
    """)
    op.execute("""
      CREATE INDEX ix_auto_work_order_assignment_mechanic
        ON auto_work_order_assignment(organization_id,location_id,mechanic_id,status,starts_at)
    """)
    op.execute("""
      CREATE TABLE auto_work_order_assignment_event (
        id uuid PRIMARY KEY,
        assignment_id uuid NOT NULL REFERENCES auto_work_order_assignment(id),
        actor_id uuid NOT NULL REFERENCES app_user(id),
        action text NOT NULL CHECK (action IN ('OFFERED','ACCEPTED','DECLINED')),
        request_key uuid NOT NULL UNIQUE,
        created_at timestamptz NOT NULL DEFAULT clock_timestamp()
      )
    """)
    op.execute("""
      CREATE FUNCTION guard_auto_work_order_assignment()
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
        IF OLD.status<>'OFFERED' OR NEW.status NOT IN ('ACCEPTED','DECLINED')
        THEN RAISE EXCEPTION 'invalid assignment transition'; END IF;
        IF NEW.status_actor_id IS DISTINCT FROM NEW.mechanic_id
        THEN RAISE EXCEPTION 'only assigned mechanic may respond'; END IF;
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
      END $f$
    """)
    op.execute("""
      CREATE TRIGGER auto_work_order_assignment_guard
      BEFORE INSERT OR UPDATE OR DELETE ON auto_work_order_assignment
      FOR EACH ROW EXECUTE FUNCTION guard_auto_work_order_assignment()
    """)
    op.execute("""
      CREATE FUNCTION guard_auto_work_order_assignment_event()
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
        THEN RAISE EXCEPTION 'assignment event mismatch'; END IF;
        RETURN NEW;
      END $f$
    """)
    op.execute("""
      CREATE TRIGGER auto_work_order_assignment_event_guard
      BEFORE INSERT ON auto_work_order_assignment_event
      FOR EACH ROW EXECUTE FUNCTION guard_auto_work_order_assignment_event()
    """)
    op.execute("""
      CREATE TRIGGER auto_work_order_assignment_event_immutable
      BEFORE UPDATE OR DELETE ON auto_work_order_assignment_event
      FOR EACH ROW EXECUTE FUNCTION immutable_auto_stock_event()
    """)
    # Enforce ownership in PG17 even when a caller bypasses the HTTP layer.
    op.execute("""
      CREATE FUNCTION guard_auto_assignment_execution()
      RETURNS trigger LANGUAGE plpgsql AS $f$
      DECLARE target_order uuid;
      DECLARE performer uuid;
      BEGIN
        IF TG_TABLE_NAME='auto_work_order_labor_entry' THEN
          SELECT work_order_id INTO target_order FROM auto_work_order_execution
            WHERE id=NEW.execution_id;
          performer:=NEW.actor_id;
        ELSIF TG_TABLE_NAME='auto_work_order_execution_finish' THEN
          SELECT work_order_id INTO target_order FROM auto_work_order_execution
            WHERE id=NEW.execution_id;
          performer:=NEW.actor_id;
        ELSIF TG_TABLE_NAME='auto_stock_mechanic_handoff' THEN
          SELECT work_order_id INTO target_order FROM auto_stock_lot_work_order_issue
            WHERE id=NEW.issue_id;
          performer:=NEW.received_by;
        ELSIF TG_TABLE_NAME='auto_work_order_qc_event' THEN
          target_order:=NEW.work_order_id;
          performer:=NEW.actor_id;
          IF EXISTS (
            SELECT 1 FROM auto_work_order_assignment a
            WHERE a.work_order_id=target_order AND a.mechanic_id=performer
              AND a.status='ACCEPTED'
          ) THEN
            RAISE EXCEPTION 'assigned executor cannot perform independent QC';
          END IF;
          RETURN NEW;
        END IF;
        IF EXISTS (
          SELECT 1 FROM auto_work_order_assignment a
          WHERE a.work_order_id=target_order
            AND (a.status<>'ACCEPTED' OR a.mechanic_id<>performer)
        ) THEN
          RAISE EXCEPTION 'accepted mechanic assignment required';
        END IF;
        RETURN NEW;
      END $f$
    """)
    for table in ("auto_work_order_labor_entry",
                  "auto_stock_mechanic_handoff",
                  "auto_work_order_execution_finish",
                  "auto_work_order_qc_event"):
        op.execute(f"""CREATE TRIGGER {table}_assignment_guard
          BEFORE INSERT ON {table} FOR EACH ROW
          EXECUTE FUNCTION guard_auto_assignment_execution()""")
    op.execute("REVOKE ALL ON auto_work_order_assignment FROM PUBLIC")
    op.execute("REVOKE ALL ON auto_work_order_assignment_event FROM PUBLIC")


def downgrade():
    for table in ("auto_work_order_qc_event",
                  "auto_work_order_execution_finish",
                  "auto_stock_mechanic_handoff",
                  "auto_work_order_labor_entry"):
        op.execute(f"DROP TRIGGER {table}_assignment_guard ON {table}")
    op.execute("DROP FUNCTION guard_auto_assignment_execution()")
    op.execute("DROP TABLE auto_work_order_assignment_event")
    op.execute("DROP TABLE auto_work_order_assignment")
    op.execute("DROP FUNCTION guard_auto_work_order_assignment_event()")
    op.execute("DROP FUNCTION guard_auto_work_order_assignment()")
