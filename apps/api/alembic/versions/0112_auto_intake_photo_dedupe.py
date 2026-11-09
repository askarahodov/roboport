"""Reject duplicate image evidence within one intake act in PostgreSQL.

Revision ID: 0112_auto_intake_photo_dedupe
Revises: 0111_auto_work_complexes
"""
from alembic import op

revision = "0112_auto_intake_photo_dedupe"
down_revision = "0111_auto_work_complexes"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
      CREATE FUNCTION guard_auto_intake_photo_dedupe()
      RETURNS trigger LANGUAGE plpgsql AS $f$
      BEGIN
        IF (SELECT count(DISTINCT p.value->>'sha256')
            FROM jsonb_array_elements(NEW.photo_meta) AS p(value))
            <> jsonb_array_length(NEW.photo_meta)
        THEN
          RAISE EXCEPTION 'duplicate intake photo digest';
        END IF;
        RETURN NEW;
      END $f$
    """)
    op.execute("""
      CREATE TRIGGER auto_intake_photo_dedupe_guard
      BEFORE INSERT ON auto_work_order_intake_act
      FOR EACH ROW EXECUTE FUNCTION guard_auto_intake_photo_dedupe()
    """)


def downgrade():
    # Disposable synthetic PG17 databases only.
    op.execute("DROP TRIGGER auto_intake_photo_dedupe_guard ON auto_work_order_intake_act")
    op.execute("DROP FUNCTION guard_auto_intake_photo_dedupe()")
