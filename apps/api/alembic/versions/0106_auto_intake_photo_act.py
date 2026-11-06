"""Atomic immutable vehicle intake act with bounded, sanitized PNG evidence.

Revision ID: 0106_auto_intake_photo_act
Revises: 0105_auto_norm_hour_quote
Synthetic-only visual evidence; no legal signature or actual vehicle release.
"""
from alembic import op

revision="0106_auto_intake_photo_act"
down_revision="0105_auto_norm_hour_quote"
branch_labels=None
depends_on=None


def upgrade():
    op.execute("""
      CREATE TABLE auto_work_order_intake_act (
        id uuid PRIMARY KEY,
        work_order_id uuid NOT NULL REFERENCES auto_work_order(id),
        inspection_id uuid NOT NULL REFERENCES auto_work_order_inspection(id),
        version smallint NOT NULL CHECK (version BETWEEN 1 AND 20),
        actor_id uuid NOT NULL REFERENCES app_user(id),
        request_key uuid NOT NULL UNIQUE,
        fingerprint text NOT NULL CHECK (fingerprint ~ '^[0-9a-f]{64}$'),
        vehicle_kind text NOT NULL CHECK (
          vehicle_kind IN ('PASSENGER','SUV','VAN','TRUCK','MOTORCYCLE')
        ),
        damages jsonb NOT NULL CHECK (
          jsonb_typeof(damages)='array' AND jsonb_array_length(damages)<=12
        ),
        inventory jsonb NOT NULL CHECK (
          jsonb_typeof(inventory)='array' AND jsonb_array_length(inventory)=5
        ),
        photo_meta jsonb NOT NULL CHECK (
          jsonb_typeof(photo_meta)='array' AND jsonb_array_length(photo_meta)<=3
        ),
        photo_bytes bytea[] NOT NULL DEFAULT ARRAY[]::bytea[],
        created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
        UNIQUE (work_order_id,version),
        CHECK (COALESCE(array_length(photo_bytes,1),0)=jsonb_array_length(photo_meta)),
        CHECK (pg_column_size(photo_bytes) <= 800000)
      )
    """)
    op.execute("""
      CREATE INDEX auto_intake_act_created_idx ON auto_work_order_intake_act
      (work_order_id,version DESC)
    """)
    op.execute("""
      CREATE FUNCTION guard_auto_intake_act_insert()
      RETURNS trigger LANGUAGE plpgsql AS $f$
      DECLARE current_version integer;
      DECLARE d jsonb;
      DECLARE i jsonb;
      DECLARE p jsonb;
      BEGIN
        IF NOT EXISTS(
          SELECT 1 FROM auto_work_order w
          JOIN auto_appointment a ON a.id=w.appointment_id
            AND a.organization_id=w.organization_id AND a.location_id=w.location_id
          JOIN LATERAL (
            SELECT COALESCE((
              SELECT r.new_booking_id FROM auto_appointment_reschedule r
              WHERE r.appointment_id=a.id
              ORDER BY r.created_at DESC,r.id DESC LIMIT 1
            ),a.booking_id) AS current_id
          ) booking ON TRUE
          JOIN auto_bay_booking b ON b.id=booking.current_id
            AND b.organization_id=w.organization_id AND b.location_id=w.location_id
            AND b.canceled_at IS NULL
          JOIN auto_vehicle_customer_link proof ON proof.id=a.customer_link_id
            AND proof.vehicle_id=a.vehicle_id
            AND proof.verified_at IS NOT NULL AND proof.revoked_at IS NULL
          JOIN auto_vehicle_location_binding binding ON binding.vehicle_id=a.vehicle_id
            AND binding.location_id=w.location_id
          JOIN auto_organization o ON o.id=w.organization_id AND o.disabled_at IS NULL
          JOIN auto_service_location loc ON loc.id=w.location_id
            AND loc.organization_id=w.organization_id AND loc.disabled_at IS NULL
          JOIN auto_work_order_inspection inspection ON inspection.id=NEW.inspection_id
            AND inspection.work_order_id=w.id
            AND inspection.organization_id=w.organization_id
            AND inspection.location_id=w.location_id
          JOIN auto_organization_role_grant g ON g.organization_id=w.organization_id
            AND g.user_id=NEW.actor_id AND g.role_code='INTAKE'
            AND g.revoked_at IS NULL
            AND (g.location_id=w.location_id OR g.location_id IS NULL)
          WHERE w.id=NEW.work_order_id
            AND NOT EXISTS(SELECT 1 FROM auto_work_order_qc_event q
              WHERE q.work_order_id=w.id)
            AND NOT EXISTS(SELECT 1 FROM auto_work_order_estimate_revision e
              WHERE e.work_order_id=w.id)
          FOR SHARE OF w,a,b,proof,binding,o,loc,inspection,g
        ) THEN RAISE EXCEPTION 'act requires inspection, live proof, staff and editable order'; END IF;

        SELECT COALESCE(MAX(version),0) INTO current_version
          FROM auto_work_order_intake_act WHERE work_order_id=NEW.work_order_id;
        IF NEW.version<>current_version+1
        THEN RAISE EXCEPTION 'intake act version mismatch'; END IF;

        FOR d IN SELECT value FROM jsonb_array_elements(NEW.damages)
        LOOP
          IF jsonb_typeof(d)<>'object'
            OR (SELECT count(*) FROM jsonb_object_keys(d))<>3
            OR COALESCE(d->>'areaCode','') NOT IN
              ('FRONT','REAR','LEFT','RIGHT','ROOF','CABIN')
            OR COALESCE(d->>'damageCode','') NOT IN
              ('SCRATCH','DENT','CRACK','MISSING','CORROSION')
            OR COALESCE(d->>'severity','') NOT IN
              ('MINOR','MODERATE','SEVERE')
          THEN RAISE EXCEPTION 'invalid damage codes'; END IF;
        END LOOP;

        IF (SELECT count(DISTINCT d->>'areaCode'||'/'||d->>'damageCode')
            FROM jsonb_array_elements(NEW.damages) d)
            <> jsonb_array_length(NEW.damages)
        THEN RAISE EXCEPTION 'duplicate damage'; END IF;

        FOR i IN SELECT value FROM jsonb_array_elements(NEW.inventory)
        LOOP
          IF jsonb_typeof(i)<>'object'
            OR (SELECT count(*) FROM jsonb_object_keys(i))<>2
            OR COALESCE(i->>'itemCode','') NOT IN
              ('KEYS','SPARE_WHEEL','TOOLS','DOCS','CHARGER')
            OR COALESCE(i->>'condition','') NOT IN
              ('PRESENT','MISSING','DAMAGED','NOT_CHECKED')
          THEN RAISE EXCEPTION 'invalid inventory code'; END IF;
        END LOOP;
        IF (SELECT count(DISTINCT i->>'itemCode')
            FROM jsonb_array_elements(NEW.inventory) i)<>5
        THEN RAISE EXCEPTION 'incomplete inventory checklist'; END IF;

        FOR p IN SELECT value FROM jsonb_array_elements(NEW.photo_meta)
        LOOP
          IF jsonb_typeof(p)<>'object'
            OR (SELECT count(*) FROM jsonb_object_keys(p))<>5
            OR COALESCE(p->>'areaCode','') NOT IN
              ('FRONT','REAR','LEFT','RIGHT','ROOF','CABIN')
            OR COALESCE(p->>'sha256','') !~ '^[0-9a-f]{64}$'
            OR COALESCE(p->>'mime','') <> 'image/png'
            OR COALESCE(p->>'width','') !~ '^[1-9][0-9]{0,3}$'
            OR COALESCE(p->>'height','') !~ '^[1-9][0-9]{0,3}$'
          THEN RAISE EXCEPTION 'invalid photo manifest'; END IF;
        END LOOP;
        RETURN NEW;
      END $f$
    """)
    op.execute("""
      CREATE TRIGGER auto_intake_act_insert_guard
      BEFORE INSERT ON auto_work_order_intake_act
      FOR EACH ROW EXECUTE FUNCTION guard_auto_intake_act_insert()
    """)
    op.execute("""
      CREATE TRIGGER auto_intake_act_immutable
      BEFORE UPDATE OR DELETE ON auto_work_order_intake_act
      FOR EACH ROW EXECUTE FUNCTION immutable_auto_stock_event()
    """)
    op.execute("REVOKE ALL ON auto_work_order_intake_act FROM PUBLIC")


def downgrade():
    # Disposable PG17 test database only, never delete actual signed evidence.
    op.execute("DROP TABLE auto_work_order_intake_act")
    op.execute("DROP FUNCTION guard_auto_intake_act_insert()")
