"""Append-only synthetic document lifecycle tied to exact intake and estimate.

Revision ID: 0118_auto_technical_document_lifecycle
Revises: 0117_auto_intake_client_review
No legal posting, payments, e-signatures or physical car handover.
"""
from alembic import op

revision = "0118_auto_technical_document_lifecycle"
down_revision = "0117_auto_intake_client_review"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
      CREATE TABLE auto_order_demo_document_event (
        id uuid PRIMARY KEY,
        work_order_id uuid NOT NULL REFERENCES auto_work_order(id),
        preview_id uuid NOT NULL REFERENCES auto_order_demo_handover_preview(id),
        preview_digest text NOT NULL CHECK (preview_digest ~ '^[0-9a-f]{64}$'),
        intake_act_id uuid NOT NULL REFERENCES auto_work_order_intake_act(id),
        intake_digest text NOT NULL CHECK (intake_digest ~ '^[0-9a-f]{64}$'),
        actor_id uuid NOT NULL REFERENCES app_user(id),
        request_key uuid NOT NULL UNIQUE,
        action text NOT NULL CHECK (
          action IN ('POSTED','CLIENT_ACK','CLIENT_DISPUTE','CLOSED_TECHNICAL')
        ),
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        UNIQUE (work_order_id,action)
      )
    """)
    op.execute("""
      CREATE UNIQUE INDEX auto_demo_document_one_client_review
      ON auto_order_demo_document_event(work_order_id)
      WHERE action IN ('CLIENT_ACK','CLIENT_DISPUTE')
    """)
    op.execute("""
      CREATE FUNCTION guard_auto_demo_document_event()
      RETURNS trigger LANGUAGE plpgsql AS $f$
      DECLARE org_id uuid;
      DECLARE loc_id uuid;
      DECLARE existing_post boolean;
      DECLARE existing_ack boolean;
      DECLARE existing_dispute boolean;
      DECLARE existing_close boolean;
      BEGIN
        -- Serialize every phase and verify the original booking/proof even
        -- for direct SQL. A guessed ID is never a grant.
        SELECT organization_id,location_id INTO org_id,loc_id
        FROM auto_work_order WHERE id=NEW.work_order_id FOR UPDATE;
        IF NOT FOUND THEN RAISE EXCEPTION 'document order unavailable'; END IF;

        IF NOT EXISTS (
          SELECT 1 FROM auto_work_order w
          JOIN auto_appointment a ON a.id=w.appointment_id
            AND a.organization_id=w.organization_id AND a.location_id=w.location_id
          JOIN auto_vehicle_customer_link proof
            ON proof.id=a.customer_link_id AND proof.vehicle_id=a.vehicle_id
            AND proof.verified_at IS NOT NULL AND proof.verified_by IS NOT NULL
            AND proof.revoked_at IS NULL
          JOIN auto_vehicle_location_binding binding
            ON binding.vehicle_id=a.vehicle_id AND binding.location_id=w.location_id
          JOIN auto_organization org ON org.id=w.organization_id
            AND org.disabled_at IS NULL
          JOIN auto_service_location loc ON loc.id=w.location_id
            AND loc.organization_id=w.organization_id AND loc.disabled_at IS NULL
          JOIN LATERAL (
            SELECT COALESCE((
              SELECT move.new_booking_id FROM auto_appointment_reschedule move
              WHERE move.appointment_id=a.id
              ORDER BY move.created_at DESC,move.id DESC LIMIT 1
            ),a.booking_id) AS current_id
          ) actual_booking ON TRUE
          JOIN auto_bay_booking booking ON booking.id=actual_booking.current_id
            AND booking.organization_id=w.organization_id
            AND booking.location_id=w.location_id AND booking.canceled_at IS NULL
          JOIN auto_order_demo_handover_preview preview
            ON preview.work_order_id=w.id AND preview.id=NEW.preview_id
          JOIN auto_work_order_intake_act act
            ON act.id=NEW.intake_act_id AND act.work_order_id=w.id
          JOIN auto_work_order_intake_act_review review
            ON review.act_id=act.id AND review.work_order_id=w.id
            AND review.decision='ACKNOWLEDGED'
            AND review.fingerprint=act.fingerprint
          WHERE w.id=NEW.work_order_id
            AND btrim(preview.fingerprint)=NEW.preview_digest
            AND encode(sha256(convert_to(preview.snapshot::text,'UTF8')),'hex')
                =NEW.preview_digest
            AND preview.snapshot->>'kind'='SYNTHETIC_PRE_HANDOVER_PREVIEW'
            AND preview.snapshot->>'simulatedDueMinor'='0'
            AND preview.snapshot->>'releaseAllowed'='false'
            AND preview.snapshot->>'legalGatePassed'='false'
            AND act.fingerprint=NEW.intake_digest
            AND act.version=(SELECT MAX(last_act.version)
                FROM auto_work_order_intake_act last_act
                WHERE last_act.work_order_id=w.id)
        ) THEN RAISE EXCEPTION 'document evidence or live order invalid'; END IF;

        IF NEW.action IN ('POSTED','CLOSED_TECHNICAL') THEN
          IF NOT EXISTS (
            SELECT 1 FROM auto_organization_role_grant g
            WHERE g.organization_id=org_id AND g.user_id=NEW.actor_id
              AND g.role_code='MANAGER' AND g.revoked_at IS NULL
              AND (g.location_id=loc_id OR g.location_id IS NULL)
          ) THEN RAISE EXCEPTION 'document manager permission missing'; END IF;
        ELSE
          IF NOT EXISTS (
            SELECT 1 FROM auto_work_order w
            JOIN auto_appointment a ON a.id=w.appointment_id
            JOIN auto_vehicle_customer_link proof
              ON proof.id=a.customer_link_id
            JOIN account_customer_link acl
              ON acl.customer_id=proof.customer_id
              AND acl.user_id=NEW.actor_id AND acl.revoked_at IS NULL
            JOIN user_role role
              ON role.user_id=NEW.actor_id AND role.role_code='CLIENT'
              AND role.revoked_at IS NULL
              AND (role.expires_at IS NULL OR role.expires_at>now())
            WHERE w.id=NEW.work_order_id
              AND proof.vehicle_id=a.vehicle_id
              AND proof.verified_at IS NOT NULL AND proof.revoked_at IS NULL
          ) THEN RAISE EXCEPTION 'document client permission missing'; END IF;
        END IF;

        SELECT EXISTS(SELECT 1 FROM auto_order_demo_document_event e
                  WHERE e.work_order_id=NEW.work_order_id AND e.action='POSTED'),
               EXISTS(SELECT 1 FROM auto_order_demo_document_event e
                  WHERE e.work_order_id=NEW.work_order_id AND e.action='CLIENT_ACK'),
               EXISTS(SELECT 1 FROM auto_order_demo_document_event e
                  WHERE e.work_order_id=NEW.work_order_id AND e.action='CLIENT_DISPUTE'),
               EXISTS(SELECT 1 FROM auto_order_demo_document_event e
                  WHERE e.work_order_id=NEW.work_order_id AND e.action='CLOSED_TECHNICAL')
        INTO existing_post,existing_ack,existing_dispute,existing_close;

        IF (NEW.action='POSTED' AND
              (existing_post OR existing_ack OR existing_dispute OR existing_close))
          OR (NEW.action IN ('CLIENT_ACK','CLIENT_DISPUTE') AND
              (NOT existing_post OR existing_ack OR existing_dispute OR existing_close))
          OR (NEW.action='CLOSED_TECHNICAL' AND
              (NOT existing_post OR NOT existing_ack OR existing_dispute OR existing_close))
        THEN RAISE EXCEPTION 'document state transition blocked'; END IF;
        RETURN NEW;
      END $f$
    """)
    op.execute("""
      CREATE TRIGGER auto_demo_document_guard
      BEFORE INSERT ON auto_order_demo_document_event
      FOR EACH ROW EXECUTE FUNCTION guard_auto_demo_document_event()
    """)
    op.execute("""
      CREATE TRIGGER auto_demo_document_immutable
      BEFORE UPDATE OR DELETE ON auto_order_demo_document_event
      FOR EACH ROW EXECUTE FUNCTION immutable_auto_stock_event()
    """)
    op.execute("REVOKE ALL ON auto_order_demo_document_event FROM PUBLIC")


def downgrade():
    # Disposable synthetic PG17 only, never delete actual customer documents.
    op.execute("DROP TABLE auto_order_demo_document_event")
    op.execute("DROP FUNCTION guard_auto_demo_document_event()")
