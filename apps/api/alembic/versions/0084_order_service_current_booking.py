"""Validate the currently active booking when selecting services after rescheduling.

Revision ID: 0084_order_service_current_booking
Revises: 0083_order_service_consent
"""
from alembic import op

revision = "0084_order_service_current_booking"
down_revision = "0083_order_service_consent"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
      CREATE OR REPLACE FUNCTION guard_auto_work_order_service()
      RETURNS trigger LANGUAGE plpgsql AS $fn$
      BEGIN
        IF NOT EXISTS(
          SELECT 1 FROM auto_work_order w
          JOIN auto_appointment a ON a.id=w.appointment_id
          JOIN LATERAL (
            SELECT COALESCE((
              SELECT rs.new_booking_id FROM auto_appointment_reschedule rs
              WHERE rs.appointment_id=a.id
              ORDER BY rs.created_at DESC,rs.id DESC LIMIT 1
            ),a.booking_id) AS current_id
          ) current_booking ON TRUE
          JOIN auto_bay_booking b ON b.id=current_booking.current_id
          JOIN auto_vehicle_customer_link link
            ON link.id=a.customer_link_id AND link.vehicle_id=a.vehicle_id
          JOIN auto_service_catalog s
            ON s.id=NEW.service_id
            AND s.organization_id=w.organization_id
            AND s.location_id=w.location_id
          JOIN auto_service_location l ON l.id=w.location_id
          JOIN auto_organization o ON o.id=w.organization_id
          WHERE w.id=NEW.work_order_id
            AND w.organization_id=NEW.organization_id
            AND w.location_id=NEW.location_id
            AND a.organization_id=w.organization_id
            AND a.location_id=w.location_id
            AND b.organization_id=w.organization_id
            AND b.location_id=w.location_id
            AND b.canceled_at IS NULL
            AND link.verified_at IS NOT NULL
            AND link.revoked_at IS NULL
            AND l.organization_id=w.organization_id
            AND l.disabled_at IS NULL AND o.disabled_at IS NULL
          FOR SHARE OF w,a,b,link,s,l,o
        ) THEN RAISE EXCEPTION 'work order service scope or proof unavailable'; END IF;
        RETURN NEW;
      END $fn$
    """)


def downgrade():
    op.execute("""
      CREATE OR REPLACE FUNCTION guard_auto_work_order_service()
      RETURNS trigger LANGUAGE plpgsql AS $fn$
      BEGIN
        IF NOT EXISTS(
          SELECT 1 FROM auto_work_order w
          JOIN auto_appointment a ON a.id=w.appointment_id
          JOIN auto_bay_booking b ON b.id=a.booking_id
          JOIN auto_vehicle_customer_link link
            ON link.id=a.customer_link_id AND link.vehicle_id=a.vehicle_id
          JOIN auto_service_catalog s
            ON s.id=NEW.service_id
            AND s.organization_id=w.organization_id
            AND s.location_id=w.location_id
          JOIN auto_service_location l ON l.id=w.location_id
          JOIN auto_organization o ON o.id=w.organization_id
          WHERE w.id=NEW.work_order_id
            AND w.organization_id=NEW.organization_id
            AND w.location_id=NEW.location_id
            AND a.organization_id=w.organization_id
            AND a.location_id=w.location_id
            AND b.organization_id=w.organization_id
            AND b.location_id=w.location_id
            AND b.canceled_at IS NULL
            AND link.verified_at IS NOT NULL
            AND link.revoked_at IS NULL
            AND l.organization_id=w.organization_id
            AND l.disabled_at IS NULL AND o.disabled_at IS NULL
          FOR SHARE OF w,a,b,link,s,l,o
        ) THEN RAISE EXCEPTION 'work order service scope or proof unavailable'; END IF;
        RETURN NEW;
      END $fn$
    """)
