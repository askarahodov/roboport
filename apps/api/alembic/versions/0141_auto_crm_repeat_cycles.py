"""Immutable CRM repeat cycles and exact public fulfillment lineage."""
from alembic import op
from sqlalchemy import text

revision="0141_auto_crm_repeat_cycles"
down_revision="0140_auto_crm_mock_messenger"
branch_labels=None
depends_on=None


def upgrade():
    op.execute("""
      ALTER TABLE auto_crm_lead ADD COLUMN visit_number integer NOT NULL DEFAULT 1
        CHECK(visit_number>=1);
      ALTER TABLE auto_crm_lead ADD COLUMN previous_lead_id uuid REFERENCES auto_crm_lead(id);
      ALTER TABLE auto_crm_lead ADD CONSTRAINT uq_crm_lead_cycle
        UNIQUE(organization_id,location_id,customer_link_id,source_code,visit_number);
      ALTER TABLE auto_crm_lead ADD CONSTRAINT uq_crm_lead_predecessor UNIQUE(previous_lead_id);
      ALTER TABLE auto_crm_lead ADD CONSTRAINT ck_crm_cycle_parent
        CHECK((visit_number=1)=(previous_lead_id IS NULL));
      DO $f$
      DECLARE constraint_name text;
      BEGIN
        SELECT conname INTO STRICT constraint_name FROM pg_constraint
          WHERE conrelid='auto_crm_lead'::regclass AND contype='u'
          AND pg_get_constraintdef(oid)='UNIQUE (organization_id, location_id, customer_link_id, source_code)';
        EXECUTE format('ALTER TABLE auto_crm_lead DROP CONSTRAINT %I',constraint_name);
      END $f$;
      CREATE FUNCTION guard_auto_crm_repeat_cycle() RETURNS trigger
      LANGUAGE plpgsql AS $f$
      DECLARE parent_row record;
      BEGIN
        IF NEW.visit_number=1 THEN RETURN NEW; END IF;
        SELECT id,organization_id,location_id,customer_link_id,source_code,visit_number
          INTO parent_row FROM auto_crm_lead WHERE id=NEW.previous_lead_id FOR SHARE;
        IF NOT FOUND OR
           (parent_row.organization_id,parent_row.location_id,parent_row.customer_link_id,parent_row.source_code,parent_row.visit_number)
           IS DISTINCT FROM
           (NEW.organization_id,NEW.location_id,NEW.customer_link_id,NEW.source_code,NEW.visit_number-1)
           OR COALESCE((SELECT stage_code FROM auto_crm_lead_stage WHERE lead_id=parent_row.id
                ORDER BY created_at DESC,id DESC LIMIT 1),'NEW') NOT IN ('BOOKED','LOST')
        THEN RAISE EXCEPTION 'CRM repeat requires terminal predecessor in same scope'; END IF;
        RETURN NEW;
      END $f$;
      CREATE TRIGGER auto_crm_repeat_cycle_guard BEFORE INSERT ON auto_crm_lead
        FOR EACH ROW EXECUTE FUNCTION guard_auto_crm_repeat_cycle();
      CREATE FUNCTION guard_auto_crm_repeat_booking() RETURNS trigger
      LANGUAGE plpgsql AS $f$
      BEGIN
        IF NEW.stage_code='BOOKED' AND EXISTS (
          SELECT 1 FROM auto_crm_lead current
          JOIN auto_crm_lead prior ON prior.organization_id=current.organization_id
            AND prior.location_id=current.location_id AND prior.customer_link_id=current.customer_link_id
            AND prior.source_code=current.source_code AND prior.visit_number<current.visit_number
          JOIN auto_crm_lead_stage stage ON stage.lead_id=prior.id
            AND stage.stage_code='BOOKED' AND stage.appointment_id=NEW.appointment_id
          WHERE current.id=NEW.lead_id
        ) THEN RAISE EXCEPTION 'CRM repeat must use a new appointment'; END IF;
        RETURN NEW;
      END $f$;
      CREATE TRIGGER auto_crm_repeat_booking_guard BEFORE INSERT ON auto_crm_lead_stage
        FOR EACH ROW EXECUTE FUNCTION guard_auto_crm_repeat_booking();
      CREATE FUNCTION guard_auto_public_crm_exact() RETURNS trigger
      LANGUAGE plpgsql AS $f$
      BEGIN
        PERFORM 1 FROM auto_crm_lead WHERE id=NEW.crm_lead_id FOR UPDATE;
        IF NOT EXISTS(
          SELECT 1 FROM auto_crm_lead lead JOIN auto_crm_lead_stage stage ON stage.lead_id=lead.id
          WHERE lead.id=NEW.crm_lead_id AND lead.source_code='WEB'
            AND stage.stage_code='BOOKED' AND stage.appointment_id=NEW.appointment_id
        ) OR EXISTS(SELECT 1 FROM auto_public_booking_fulfillment WHERE crm_lead_id=NEW.crm_lead_id)
        THEN RAISE EXCEPTION 'public CRM requires exact unique booked lineage'; END IF;
        RETURN NEW;
      END $f$;
      CREATE TRIGGER auto_public_crm_exact_guard BEFORE INSERT ON auto_public_booking_fulfillment
        FOR EACH ROW EXECUTE FUNCTION guard_auto_public_crm_exact();
    """)


def downgrade():
    if op.get_bind().execute(text("SELECT 1 FROM auto_crm_lead WHERE visit_number>1 LIMIT 1")).first():
        raise RuntimeError("CRM repeat evidence exists: restore isolated synthetic backup")
    op.execute("""
      DROP TRIGGER auto_public_crm_exact_guard ON auto_public_booking_fulfillment;
      DROP FUNCTION guard_auto_public_crm_exact();
      DROP TRIGGER auto_crm_repeat_booking_guard ON auto_crm_lead_stage;
      DROP FUNCTION guard_auto_crm_repeat_booking();
      DROP TRIGGER auto_crm_repeat_cycle_guard ON auto_crm_lead;
      DROP FUNCTION guard_auto_crm_repeat_cycle();
      ALTER TABLE auto_crm_lead ADD CONSTRAINT uq_crm_lead_original_scope
        UNIQUE(organization_id,location_id,customer_link_id,source_code);
      ALTER TABLE auto_crm_lead DROP CONSTRAINT uq_crm_lead_cycle;
      ALTER TABLE auto_crm_lead DROP CONSTRAINT uq_crm_lead_predecessor;
      ALTER TABLE auto_crm_lead DROP CONSTRAINT ck_crm_cycle_parent;
      ALTER TABLE auto_crm_lead DROP COLUMN previous_lead_id;
      ALTER TABLE auto_crm_lead DROP COLUMN visit_number;
    """)
