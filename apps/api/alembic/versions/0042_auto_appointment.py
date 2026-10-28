"""Synthetic appointment identity tied to verified vehicle/customer and one bay booking.

Revision ID: 0042_auto_appointment
Revises: 0041_auto_bay_lock
"""
from alembic import op

revision = "0042_auto_appointment"
down_revision = "0041_auto_bay_lock"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
      CREATE TABLE auto_appointment (
        id uuid PRIMARY KEY,
        booking_id uuid NOT NULL UNIQUE REFERENCES auto_bay_booking(id),
        organization_id uuid NOT NULL REFERENCES auto_organization(id),
        location_id uuid NOT NULL REFERENCES auto_service_location(id),
        vehicle_id uuid NOT NULL REFERENCES auto_vehicle(id),
        customer_link_id uuid NOT NULL REFERENCES auto_vehicle_customer_link(id),
        created_at timestamptz NOT NULL DEFAULT now()
      )
    """)
    op.execute("""
      CREATE FUNCTION guard_auto_appointment()
      RETURNS trigger LANGUAGE plpgsql AS $fn$
      BEGIN
        IF TG_OP <> 'INSERT' THEN
          RAISE EXCEPTION 'appointment identity/history immutable';
        END IF;
        IF NOT EXISTS (
          SELECT 1 FROM auto_bay_booking b
          JOIN auto_vehicle_customer_link link
            ON link.id=NEW.customer_link_id AND link.vehicle_id=NEW.vehicle_id
          WHERE b.id=NEW.booking_id AND b.organization_id=NEW.organization_id
            AND b.location_id=NEW.location_id AND b.canceled_at IS NULL
            AND link.verified_at IS NOT NULL AND link.revoked_at IS NULL
          FOR SHARE OF b, link
        ) THEN
          RAISE EXCEPTION 'appointment booking or verified vehicle link unavailable';
        END IF;
        RETURN NEW;
      END $fn$
    """)
    op.execute("""
      CREATE TRIGGER auto_appointment_guard BEFORE INSERT OR UPDATE OR DELETE
      ON auto_appointment FOR EACH ROW EXECUTE FUNCTION guard_auto_appointment()
    """)


def downgrade():
    # Only disposable synthetic databases.
    op.execute("DROP TABLE auto_appointment")
    op.execute("DROP FUNCTION guard_auto_appointment()")
