"""Synthetic post-service review: one immutable CRM task per QC-passed order.

Revision ID: 0100_auto_crm_post_service_review
Revises: 0099_auto_crm_followups
"""
from alembic import op
from sqlalchemy import text

revision="0100_auto_crm_post_service_review"
down_revision="0099_auto_crm_followups"
branch_labels=None
depends_on=None


def upgrade():
    op.execute("""ALTER TABLE auto_crm_followup_task
      ADD COLUMN origin_work_order_id uuid REFERENCES auto_work_order(id)""")
    op.execute("""ALTER TABLE auto_crm_followup_task
      ADD CONSTRAINT ck_auto_crm_review_origin
      CHECK (origin_work_order_id IS NULL OR task_code='SERVICE_REVIEW')""")
    op.execute("""CREATE UNIQUE INDEX ux_auto_crm_followup_origin_order
      ON auto_crm_followup_task(origin_work_order_id)
      WHERE origin_work_order_id IS NOT NULL""")
    op.execute("""
      CREATE OR REPLACE FUNCTION guard_auto_crm_followup_task() RETURNS trigger
      LANGUAGE plpgsql AS $f$
      DECLARE lead_row record; current_stage text;
      BEGIN
        IF TG_OP<>'INSERT' THEN
          RAISE EXCEPTION 'CRM follow-up tasks are immutable'; END IF;
        SELECT organization_id,location_id,customer_link_id,owner_id
          INTO lead_row FROM auto_crm_lead WHERE id=NEW.lead_id FOR UPDATE;
        IF NOT FOUND THEN RAISE EXCEPTION 'CRM lead unavailable'; END IF;
        IF NEW.owner_id<>lead_row.owner_id THEN
          RAISE EXCEPTION 'CRM follow-up must belong to lead owner'; END IF;
        IF NOT EXISTS(
          SELECT 1 FROM auto_vehicle_customer_link proof
          JOIN auto_vehicle_location_binding binding
            ON binding.vehicle_id=proof.vehicle_id
            AND binding.location_id=lead_row.location_id
          JOIN auto_service_location loc
            ON loc.id=binding.location_id
            AND loc.organization_id=lead_row.organization_id
            AND loc.disabled_at IS NULL
          JOIN auto_organization org ON org.id=loc.organization_id
            AND org.disabled_at IS NULL
          WHERE proof.id=lead_row.customer_link_id
            AND proof.verified_at IS NOT NULL AND proof.verified_by IS NOT NULL
            AND proof.revoked_at IS NULL
          FOR SHARE OF proof,loc,org
        ) THEN RAISE EXCEPTION 'CRM follow-up customer proof unavailable'; END IF;
        SELECT stage_code INTO current_stage FROM auto_crm_lead_stage
          WHERE lead_id=NEW.lead_id ORDER BY created_at DESC,id DESC LIMIT 1;
        IF NEW.origin_work_order_id IS NULL THEN
          IF current_stage IN ('BOOKED','LOST') THEN
            RAISE EXCEPTION 'CRM follow-up requires active lead'; END IF;
        ELSE
          IF NEW.task_code<>'SERVICE_REVIEW' OR current_stage IS DISTINCT FROM 'BOOKED'
             OR NOT EXISTS (
               SELECT 1 FROM auto_work_order w
               JOIN auto_appointment a ON a.id=w.appointment_id
               JOIN auto_work_order_execution e ON e.work_order_id=w.id
               JOIN auto_work_order_execution_finish f ON f.execution_id=e.id
               WHERE w.id=NEW.origin_work_order_id
                 AND w.organization_id=lead_row.organization_id
                 AND w.location_id=lead_row.location_id
                 AND a.organization_id=w.organization_id
                 AND a.location_id=w.location_id
                 AND a.customer_link_id=lead_row.customer_link_id
                 AND EXISTS(
                   SELECT 1 FROM auto_crm_lead_stage stage
                   WHERE stage.lead_id=NEW.lead_id AND stage.stage_code='BOOKED'
                     AND stage.appointment_id=a.id
                 )
                 AND EXISTS(
                   SELECT 1 FROM auto_work_order_qc_event qc
                   WHERE qc.work_order_id=w.id AND qc.result='PASS'
                     AND qc.created_at>=f.created_at
                     AND NOT EXISTS(
                       SELECT 1 FROM auto_work_order_qc_event newer
                       WHERE newer.work_order_id=w.id
                         AND (newer.created_at,newer.id)>(qc.created_at,qc.id)
                     )
                 )
               FOR SHARE OF w,a,e,f
             )
          THEN RAISE EXCEPTION 'CRM service review requires finished QC-passed booked order';
          END IF;
        END IF;
        RETURN NEW;
      END $f$
    """)


def downgrade():
    bind=op.get_bind()
    if bind.execute(text("""SELECT 1 FROM auto_crm_followup_task
                             WHERE origin_work_order_id IS NOT NULL LIMIT 1""")).first():
        raise RuntimeError("CRM review evidence exists: restore synthetic backup only")
    op.execute("""
      CREATE OR REPLACE FUNCTION guard_auto_crm_followup_task() RETURNS trigger
      LANGUAGE plpgsql AS $f$
      DECLARE lead_row record; current_stage text;
      BEGIN
        IF TG_OP<>'INSERT' THEN
          RAISE EXCEPTION 'CRM follow-up tasks are immutable'; END IF;
        SELECT organization_id,location_id,customer_link_id,owner_id
          INTO lead_row FROM auto_crm_lead WHERE id=NEW.lead_id FOR UPDATE;
        IF NOT FOUND THEN RAISE EXCEPTION 'CRM lead unavailable'; END IF;
        IF NEW.owner_id<>lead_row.owner_id THEN
          RAISE EXCEPTION 'CRM follow-up must belong to lead owner'; END IF;
        IF NOT EXISTS(
          SELECT 1 FROM auto_vehicle_customer_link proof
          JOIN auto_vehicle_location_binding binding
            ON binding.vehicle_id=proof.vehicle_id
            AND binding.location_id=lead_row.location_id
          JOIN auto_service_location loc ON loc.id=binding.location_id
            AND loc.organization_id=lead_row.organization_id
            AND loc.disabled_at IS NULL
          JOIN auto_organization org ON org.id=loc.organization_id
            AND org.disabled_at IS NULL
          WHERE proof.id=lead_row.customer_link_id
            AND proof.verified_at IS NOT NULL AND proof.verified_by IS NOT NULL
            AND proof.revoked_at IS NULL
          FOR SHARE OF proof,loc,org
        ) THEN RAISE EXCEPTION 'CRM follow-up customer proof unavailable'; END IF;
        SELECT stage_code INTO current_stage FROM auto_crm_lead_stage
          WHERE lead_id=NEW.lead_id ORDER BY created_at DESC,id DESC LIMIT 1;
        IF current_stage IN ('BOOKED','LOST') THEN
          RAISE EXCEPTION 'CRM follow-up requires active lead'; END IF;
        RETURN NEW;
      END $f$
    """)
    op.execute("DROP INDEX ux_auto_crm_followup_origin_order")
    op.execute("ALTER TABLE auto_crm_followup_task DROP CONSTRAINT ck_auto_crm_review_origin")
    op.execute("ALTER TABLE auto_crm_followup_task DROP COLUMN origin_work_order_id")
