"""Prevent overlapping active staff shifts with atomic PostgreSQL GiST exclusion.

Revision ID: 0039_auto_shift_conflict
Revises: 0038_auto_workshop
"""
from alembic import op

revision = "0039_auto_shift_conflict"
down_revision = "0038_auto_workshop"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")
    op.execute("""
      ALTER TABLE auto_staff_shift ADD CONSTRAINT ex_auto_staff_active_shift_overlap
      EXCLUDE USING gist (
        user_id WITH =,
        tstzrange(starts_at, ends_at, '[)') WITH &&
      ) WHERE (canceled_at IS NULL)
    """)


def downgrade():
    # Disposable synthetic PG17 only; the extension may be shared.
    op.execute("ALTER TABLE auto_staff_shift DROP CONSTRAINT ex_auto_staff_active_shift_overlap")
