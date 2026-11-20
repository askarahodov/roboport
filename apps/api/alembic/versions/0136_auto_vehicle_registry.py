"""Защищённый registry write для филиальных автомобилей и неизменяемый аудит.

Revision ID: 0136_auto_vehicle_registry
Revises: 0135_auto_org_staff_invitation

Только synthetic/dev. VIN после первоначального заполнения неизменяем.
"""
from alembic import op

revision = "0136_auto_vehicle_registry"
down_revision = "0135_auto_org_staff_invitation"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
      ALTER TABLE auto_create_receipt
      DROP CONSTRAINT auto_create_receipt_operation_check
    """)
    op.execute("""
      ALTER TABLE auto_create_receipt
      ADD CONSTRAINT auto_create_receipt_operation_check
      CHECK (operation IN (
        'vehicle.link.propose',
        'vehicle.intake.grant',
        'vehicle.registry.create',
        'vehicle.registry.update'
      ))
    """)

    op.execute("""
      CREATE TABLE auto_vehicle_registry_audit (
        id uuid PRIMARY KEY,
        vehicle_id uuid NOT NULL
          REFERENCES auto_vehicle(id) ON DELETE RESTRICT,
        link_id uuid REFERENCES auto_vehicle_customer_link(id) ON DELETE RESTRICT,
        organization_id uuid NOT NULL
          REFERENCES auto_organization(id) ON DELETE RESTRICT,
        location_id uuid NOT NULL
          REFERENCES auto_service_location(id) ON DELETE RESTRICT,
        actor_id uuid NOT NULL REFERENCES app_user(id) ON DELETE RESTRICT,
        action text NOT NULL CHECK (action IN ('CREATED','UPDATED')),
        changed_fields text[] NOT NULL
          CHECK (cardinality(changed_fields) BETWEEN 1 AND 5)
          CHECK (changed_fields <@ ARRAY[
            'vin','make','model','variant','model_year'
          ]::text[]),
        created_at timestamptz NOT NULL DEFAULT now(),
        CHECK (
          (action='CREATED' AND link_id IS NOT NULL)
          OR (action='UPDATED' AND link_id IS NULL)
        )
      )
    """)
    op.execute("""
      CREATE INDEX ix_auto_vehicle_registry_audit_vehicle
      ON auto_vehicle_registry_audit(vehicle_id,created_at DESC,id DESC)
    """)
    op.execute("""
      CREATE INDEX ix_auto_vehicle_registry_audit_tenant
      ON auto_vehicle_registry_audit(
        organization_id,location_id,created_at DESC,id DESC
      )
    """)

    op.execute("""
      CREATE FUNCTION guard_auto_vehicle_registry_audit_scope()
      RETURNS trigger LANGUAGE plpgsql AS $fn$
      BEGIN
        IF NOT EXISTS (
          SELECT 1
          FROM auto_vehicle_location_binding b
          JOIN auto_service_location l
            ON l.id=b.location_id
           AND l.organization_id=NEW.organization_id
           AND l.disabled_at IS NULL
          JOIN auto_organization o
            ON o.id=l.organization_id AND o.disabled_at IS NULL
          WHERE b.vehicle_id=NEW.vehicle_id
            AND b.location_id=NEW.location_id
        ) THEN
          RAISE EXCEPTION 'vehicle registry audit scope mismatch';
        END IF;
        IF NOT EXISTS (
          SELECT 1 FROM app_user u
          WHERE u.id=NEW.actor_id AND u.status='ACTIVE'
        ) THEN
          RAISE EXCEPTION 'vehicle registry audit actor unavailable';
        END IF;
        IF NEW.action='CREATED' AND NOT EXISTS (
          SELECT 1 FROM auto_vehicle_customer_link p
          WHERE p.id=NEW.link_id AND p.vehicle_id=NEW.vehicle_id
        ) THEN
          RAISE EXCEPTION 'vehicle registry audit link mismatch';
        END IF;
        RETURN NEW;
      END $fn$
    """)
    op.execute("""
      CREATE TRIGGER auto_vehicle_registry_audit_scope_guard
      BEFORE INSERT ON auto_vehicle_registry_audit
      FOR EACH ROW EXECUTE FUNCTION guard_auto_vehicle_registry_audit_scope()
    """)
    op.execute("""
      CREATE TRIGGER auto_vehicle_registry_audit_immutable
      BEFORE UPDATE OR DELETE ON auto_vehicle_registry_audit
      FOR EACH ROW EXECUTE FUNCTION reject_audit_mutation()
    """)

    op.execute("""
      CREATE FUNCTION guard_auto_vehicle_registry_update()
      RETURNS trigger LANGUAGE plpgsql AS $fn$
      BEGIN
        IF (NEW.id,NEW.created_at)
           IS DISTINCT FROM (OLD.id,OLD.created_at) THEN
          RAISE EXCEPTION 'auto vehicle identity immutable';
        END IF;
        IF OLD.vin IS NOT NULL AND NEW.vin IS DISTINCT FROM OLD.vin THEN
          RAISE EXCEPTION 'auto vehicle VIN immutable after initial set';
        END IF;
        RETURN NEW;
      END $fn$
    """)
    op.execute("""
      CREATE TRIGGER auto_vehicle_registry_update_guard
      BEFORE UPDATE ON auto_vehicle
      FOR EACH ROW EXECUTE FUNCTION guard_auto_vehicle_registry_update()
    """)


def downgrade():
    # Откат допустим только на disposable PG без сохранённых registry/audit данных.
    op.execute("""
      DO $$
      BEGIN
        IF EXISTS (SELECT 1 FROM auto_vehicle_registry_audit) THEN
          RAISE EXCEPTION 'vehicle registry audit exists; export before downgrade';
        END IF;
        IF EXISTS (
          SELECT 1 FROM auto_create_receipt
          WHERE operation IN ('vehicle.registry.create','vehicle.registry.update')
        ) THEN
          RAISE EXCEPTION 'vehicle registry receipts exist; export before downgrade';
        END IF;
      END $$;
    """)
    op.execute("DROP TRIGGER auto_vehicle_registry_update_guard ON auto_vehicle")
    op.execute("DROP FUNCTION guard_auto_vehicle_registry_update()")
    op.execute("""
      DROP TRIGGER auto_vehicle_registry_audit_immutable
      ON auto_vehicle_registry_audit
    """)
    op.execute("""
      DROP TRIGGER auto_vehicle_registry_audit_scope_guard
      ON auto_vehicle_registry_audit
    """)
    op.execute("DROP FUNCTION guard_auto_vehicle_registry_audit_scope()")
    op.execute("DROP TABLE auto_vehicle_registry_audit")
    op.execute("""
      ALTER TABLE auto_create_receipt
      DROP CONSTRAINT auto_create_receipt_operation_check
    """)
    op.execute("""
      ALTER TABLE auto_create_receipt
      ADD CONSTRAINT auto_create_receipt_operation_check
      CHECK (operation IN ('vehicle.link.propose','vehicle.intake.grant'))
    """)
