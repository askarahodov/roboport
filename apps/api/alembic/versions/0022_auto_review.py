"""Журнал решения о связи машины с клиентом без изменения robot history.

Revision ID: 0022_auto_review
Revises: 0021_auto_vehicle
"""
from alembic import op

revision = "0022_auto_review"
down_revision = "0021_auto_vehicle"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
      CREATE TABLE auto_vehicle_link_review (
        id uuid PRIMARY KEY,
        link_id uuid NOT NULL REFERENCES auto_vehicle_customer_link(id) ON DELETE RESTRICT,
        reviewer_id uuid NOT NULL REFERENCES app_user(id) ON DELETE RESTRICT,
        decision text NOT NULL CHECK (decision IN ('APPROVE','REJECT')),
        evidence_ref text NOT NULL CHECK (length(btrim(evidence_ref)) BETWEEN 8 AND 100),
        created_at timestamptz NOT NULL DEFAULT now()
      )
    """)
    op.execute("""
      CREATE UNIQUE INDEX ux_auto_vehicle_link_review_one_decision
      ON auto_vehicle_link_review(link_id)
    """)
    op.execute("""
      CREATE FUNCTION protect_auto_link_review() RETURNS trigger LANGUAGE plpgsql AS $body$
      BEGIN
        RAISE EXCEPTION 'auto vehicle review is append-only';
      END $body$;
    """)
    op.execute("""
      CREATE TRIGGER auto_link_review_immutable
      BEFORE UPDATE OR DELETE ON auto_vehicle_link_review
      FOR EACH ROW EXECUTE FUNCTION protect_auto_link_review()
    """)


def downgrade():
    # Откат разрешён только на disposable dev БД без реальных данных.
    op.execute("DROP TABLE auto_vehicle_link_review")
    op.execute("DROP FUNCTION protect_auto_link_review()")
