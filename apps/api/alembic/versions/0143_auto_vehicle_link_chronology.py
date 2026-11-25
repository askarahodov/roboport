"""Хронология подтверждения и отзыва автомобильной связи (#112/#165)."""
from alembic import op

revision = "0143_auto_vehicle_link_chronology"
down_revision = "0142_auto_crm_interactions"
branch_labels = None
depends_on = None


def upgrade():
    # Валидируем все существующие строки, не переписывая исторические отметки.
    op.create_check_constraint(
        "ck_auto_vehicle_link_chronology",
        "auto_vehicle_customer_link",
        "revoked_at IS NULL OR verified_at IS NULL OR revoked_at >= verified_at",
    )


def downgrade():
    op.drop_constraint(
        "ck_auto_vehicle_link_chronology", "auto_vehicle_customer_link", type_="check",
    )
