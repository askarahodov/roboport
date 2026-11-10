"""Immutable, session-bound client review of a specific synthetic intake act.

Revision ID: 0117_auto_intake_client_review
Revises: 0116_auto_supplier_purchase
This is NOT a handwritten/electronic legal signature or car handover.
"""
from alembic import op

revision = "0117_auto_intake_client_review"
down_revision = "0116_auto_supplier_purchase"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
      CREATE TABLE auto_work_order_intake_act_review (
        id uuid PRIMARY KEY,
        work_order_id uuid NOT NULL REFERENCES auto_work_order(id),
        act_id uuid NOT NULL UNIQUE REFERENCES auto_work_order_intake_act(id),
        actor_id uuid NOT NULL REFERENCES app_user(id),
        request_key uuid NOT NULL UNIQUE,
        fingerprint text NOT NULL CHECK (fingerprint ~ '^[0-9a-f]{64}$'),
        decision text NOT NULL CHECK (decision IN ('ACKNOWLEDGED','DISPUTED')),
        reason_code text NOT NULL CHECK (
          (decision='ACKNOWLEDGED' AND reason_code='NONE')
          OR (decision='DISPUTED' AND
              reason_code IN ('DAMAGE_MISMATCH','INVENTORY_MISMATCH','OTHER'))
        ),
        created_at timestamptz NOT NULL DEFAULT clock_timestamp()
      )
    """)
    op.execute("""
      CREATE FUNCTION guard_auto_intake_act_review()
      RETURNS trigger LANGUAGE plpgsql AS $f$
      BEGIN
        -- Validate the ORIGINAL appointment's exact verified link, not VIN or
        -- the vehicle's latest owner. Enforce live CLIENT rights even for
        -- direct SQL inserts. Lock WorkOrder so review and next act revision
        -- cannot race to produce a stale acknowledgement.
        IF NOT EXISTS (
          SELECT 1 FROM auto_work_order_intake_act act
          JOIN auto_work_order w ON w.id=act.work_order_id
          JOIN auto_appointment a ON a.id=w.appointment_id
            AND a.organization_id=w.organization_id
            AND a.location_id=w.location_id
          JOIN auto_vehicle_customer_link proof
            ON proof.id=a.customer_link_id AND proof.vehicle_id=a.vehicle_id
            AND proof.verified_at IS NOT NULL AND proof.verified_by IS NOT NULL
            AND proof.revoked_at IS NULL
          JOIN account_customer_link acl ON acl.customer_id=proof.customer_id
            AND acl.user_id=NEW.actor_id AND acl.revoked_at IS NULL
          JOIN user_role role ON role.user_id=NEW.actor_id
            AND role.role_code='CLIENT' AND role.revoked_at IS NULL
            AND (role.expires_at IS NULL OR role.expires_at>now())
          JOIN auto_vehicle_location_binding binding
            ON binding.vehicle_id=a.vehicle_id AND binding.location_id=w.location_id
          JOIN auto_organization org ON org.id=w.organization_id
            AND org.disabled_at IS NULL
          JOIN auto_service_location loc ON loc.id=w.location_id
            AND loc.organization_id=w.organization_id AND loc.disabled_at IS NULL
          JOIN LATERAL (
            SELECT COALESCE((
              SELECT rs.new_booking_id FROM auto_appointment_reschedule rs
              WHERE rs.appointment_id=a.id
              ORDER BY rs.created_at DESC,rs.id DESC LIMIT 1
            ),a.booking_id) AS current_id
          ) current_booking ON TRUE
          JOIN auto_bay_booking booking ON booking.id=current_booking.current_id
            AND booking.organization_id=w.organization_id
            AND booking.location_id=w.location_id AND booking.canceled_at IS NULL
          WHERE act.id=NEW.act_id AND act.work_order_id=NEW.work_order_id
            AND act.fingerprint=NEW.fingerprint
            AND act.version=(
              SELECT max(latest.version) FROM auto_work_order_intake_act latest
              WHERE latest.work_order_id=w.id
            )
          FOR UPDATE OF w
        ) THEN
          RAISE EXCEPTION 'review requires live original owner and latest act';
        END IF;
        RETURN NEW;
      END $f$
    """)
    op.execute("""
      CREATE TRIGGER auto_intake_review_guard
      BEFORE INSERT ON auto_work_order_intake_act_review
      FOR EACH ROW EXECUTE FUNCTION guard_auto_intake_act_review()
    """)
    op.execute("""
      CREATE TRIGGER auto_intake_review_immutable
      BEFORE UPDATE OR DELETE ON auto_work_order_intake_act_review
      FOR EACH ROW EXECUTE FUNCTION immutable_auto_stock_event()
    """)
    op.execute("""
      CREATE FUNCTION guard_auto_intake_no_post_ack_revision()
      RETURNS trigger LANGUAGE plpgsql AS $f$
      BEGIN
        -- An acknowledged immutable version cannot be silently replaced.
        -- DISPUTED versions may be corrected via an entirely new revision,
        -- before estimate/QC freeze according to the existing act guard.
        PERFORM 1 FROM auto_work_order w WHERE w.id=NEW.work_order_id FOR UPDATE;
        IF EXISTS (
          SELECT 1 FROM auto_work_order_intake_act_review review
          WHERE review.work_order_id=NEW.work_order_id
            AND review.decision='ACKNOWLEDGED'
        ) THEN
          RAISE EXCEPTION 'acknowledged intake act is frozen';
        END IF;
        RETURN NEW;
      END $f$
    """)
    op.execute("""
      CREATE TRIGGER zz_auto_intake_no_post_ack_revision
      BEFORE INSERT ON auto_work_order_intake_act
      FOR EACH ROW EXECUTE FUNCTION guard_auto_intake_no_post_ack_revision()
    """)
    op.execute("REVOKE ALL ON auto_work_order_intake_act_review FROM PUBLIC")


def downgrade():
    # Disposable synthetic DB only. Never destroy real signed evidence.
    op.execute("DROP TRIGGER zz_auto_intake_no_post_ack_revision ON auto_work_order_intake_act")
    op.execute("DROP FUNCTION guard_auto_intake_no_post_ack_revision()")
    op.execute("DROP TABLE auto_work_order_intake_act_review")
    op.execute("DROP FUNCTION guard_auto_intake_act_review()")
