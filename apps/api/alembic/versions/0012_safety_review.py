"""Independent, immutable expert reviews for SAFETY_HOLD.

Revision ID: 0012_safety_review
Revises: 0011_ticket_transition_actor
"""
from alembic import op
revision = "0012_safety_review"
down_revision = "0011_ticket_transition_actor"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE TABLE ticket_safety_review(
        id uuid PRIMARY KEY,
        ticket_id uuid NOT NULL REFERENCES service_ticket(id) ON DELETE RESTRICT,
        ticket_version integer NOT NULL CHECK (ticket_version > 0),
        actor_id uuid NOT NULL REFERENCES app_user(id) ON DELETE RESTRICT,
        role_code text NOT NULL CHECK (role_code IN ('ENGINEER','MANAGER')),
        approved boolean NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE(ticket_id,ticket_version,actor_id)
    )""")
    op.execute("""CREATE FUNCTION reject_ticket_safety_review_mutation() RETURNS trigger
      LANGUAGE plpgsql AS $body$
      BEGIN
        RAISE EXCEPTION 'safety reviews are append-only';
      END $body$""")
    op.execute("""CREATE TRIGGER safety_review_append_only
      BEFORE UPDATE OR DELETE ON ticket_safety_review
      FOR EACH ROW EXECUTE FUNCTION reject_ticket_safety_review_mutation()""")


def downgrade():
    op.execute("DROP TABLE ticket_safety_review")
    op.execute("DROP FUNCTION reject_ticket_safety_review_mutation()")
