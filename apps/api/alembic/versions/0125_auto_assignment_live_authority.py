"""Live accepted assignment authorization for execution and direct SQL.

Revision ID: 0125_auto_assignment_live_authority
Revises: 0124_auto_catalog

No data deletion, existing event history immutable. Downgrade restores the
legacy trigger, so never downgrade a deployed instance without approval.
"""
from alembic import op

revision = "0125_auto_assignment_live_authority"
down_revision = "0124_auto_catalog"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
      CREATE OR REPLACE FUNCTION guard_auto_assignment_execution()
      RETURNS trigger LANGUAGE plpgsql AS $f$
      DECLARE target_order uuid;
      DECLARE performer uuid;
      DECLARE latest_row auto_work_order_assignment%ROWTYPE;
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
        -- Preserve legacy compatibility when an order was never assigned.
        -- Immutable historical DECLINED offers are not current authority.
        SELECT a.* INTO latest_row FROM auto_work_order_assignment a
          WHERE a.work_order_id=target_order
          ORDER BY a.created_at DESC,a.id DESC LIMIT 1 FOR SHARE;
        IF FOUND THEN
          IF latest_row.status<>'ACCEPTED' OR latest_row.mechanic_id<>performer
          THEN
            RAISE EXCEPTION 'accepted mechanic assignment required';
          END IF;
          -- An accepted record does not survive a canceled shift, revoked
          -- grant, disabled branch or inactive user as work authority.
          -- SHARE locks serialize with live cancellations and revocations.
          IF NOT EXISTS (
            SELECT 1 FROM auto_staff_shift shift
            JOIN auto_organization_role_grant g
              ON g.organization_id=shift.organization_id
             AND g.user_id=shift.user_id
             AND g.role_code='MECHANIC' AND g.revoked_at IS NULL
             AND (g.location_id=shift.location_id OR g.location_id IS NULL)
            JOIN app_user staff ON staff.id=shift.user_id
              AND staff.status='ACTIVE'
            JOIN auto_organization organization
              ON organization.id=shift.organization_id
              AND organization.disabled_at IS NULL
            JOIN auto_service_location location
              ON location.id=shift.location_id
              AND location.organization_id=organization.id
              AND location.disabled_at IS NULL
            WHERE shift.organization_id=latest_row.organization_id
              AND shift.location_id=latest_row.location_id
              AND shift.user_id=performer AND shift.canceled_at IS NULL
              AND shift.starts_at<=latest_row.starts_at
              AND shift.ends_at>=latest_row.ends_at
              AND latest_row.ends_at>clock_timestamp()
            FOR SHARE OF shift,g,staff,organization,location
          ) THEN
            RAISE EXCEPTION 'accepted assignment authority revoked';
          END IF;
        END IF;
        RETURN NEW;
      END $f$
    """)


def downgrade():
    # Restore the exact legacy 0108 guard, but this reopens stale-shift
    # execution: unsafe as operational rollback outside synthetic testing.
    op.execute("""
      CREATE OR REPLACE FUNCTION guard_auto_assignment_execution()
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
          SELECT 1 FROM (
            SELECT a.status,a.mechanic_id
            FROM auto_work_order_assignment a
            WHERE a.work_order_id=target_order
            ORDER BY a.created_at DESC,a.id DESC LIMIT 1
          ) latest
          WHERE latest.status<>'ACCEPTED' OR latest.mechanic_id<>performer
        ) THEN
          RAISE EXCEPTION 'accepted mechanic assignment required';
        END IF;
        RETURN NEW;
      END $f$
    """)
