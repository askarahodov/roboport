"""Аддитивная автомобильная модель на синтетическом PostgreSQL 17.

Revision ID: 0021_auto_vehicle
Revises: 0020_guest_proof

Исторические данные роботов и заявок не изменяются. Связь с автомобилем
сама по себе не предоставляет доступ к старым заказам.
"""
from alembic import op

revision = "0021_auto_vehicle"
down_revision = "0020_guest_proof"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
      CREATE TABLE auto_vehicle (
        id uuid PRIMARY KEY,
        vin varchar(17) UNIQUE,
        make text NOT NULL CHECK (length(btrim(make)) BETWEEN 1 AND 100),
        model text NOT NULL CHECK (length(btrim(model)) BETWEEN 1 AND 120),
        variant text CHECK (variant IS NULL OR length(btrim(variant)) BETWEEN 1 AND 160),
        model_year integer CHECK (model_year IS NULL OR model_year BETWEEN 1886 AND 2100),
        created_at timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT ck_auto_vehicle_vin_format
          CHECK (vin IS NULL OR vin ~ '^[A-HJ-NPR-Z0-9]{17}$')
      )
    """)
    op.execute("""
      CREATE TABLE auto_vehicle_customer_link (
        id uuid PRIMARY KEY,
        vehicle_id uuid NOT NULL REFERENCES auto_vehicle(id) ON DELETE RESTRICT,
        customer_id uuid NOT NULL REFERENCES customer(id) ON DELETE RESTRICT,
        linked_at timestamptz NOT NULL DEFAULT now(),
        verified_at timestamptz,
        verified_by uuid REFERENCES app_user(id) ON DELETE RESTRICT,
        revoked_at timestamptz,
        CHECK (verified_at IS NULL OR verified_at >= linked_at),
        CHECK ((verified_at IS NULL AND verified_by IS NULL)
            OR (verified_at IS NOT NULL AND verified_by IS NOT NULL)),
        CHECK (revoked_at IS NULL OR revoked_at >= linked_at)
      )
    """)
    op.execute("""
      CREATE UNIQUE INDEX ux_auto_vehicle_customer_active
      ON auto_vehicle_customer_link(vehicle_id, customer_id)
      WHERE revoked_at IS NULL
    """)
    op.execute("""
      CREATE INDEX ix_auto_vehicle_customer_active
      ON auto_vehicle_customer_link(customer_id, vehicle_id)
      WHERE revoked_at IS NULL AND verified_at IS NOT NULL
    """)


def downgrade():
    # Без отдельного разрешения на потерю данных откат допускается только в dev.
    op.execute("DROP TABLE auto_vehicle_customer_link")
    op.execute("DROP TABLE auto_vehicle")
