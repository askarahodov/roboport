"""Persist actor identity on ticket transition events.

Revision ID: 0011_ticket_transition_actor
Revises: 0010_estimate_acl
"""
from alembic import op

revision = "0011_ticket_transition_actor"
down_revision = "0010_estimate_acl"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""ALTER TABLE ticket_state_event
                  ADD COLUMN actor_id uuid REFERENCES app_user(id) ON DELETE RESTRICT""")
    op.execute("""CREATE FUNCTION reject_ticket_event_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $body$
        BEGIN
          RAISE EXCEPTION 'ticket events are append-only';
        END $body$""")
    op.execute("""CREATE TRIGGER ticket_state_event_immutable
        BEFORE UPDATE OR DELETE ON ticket_state_event
        FOR EACH ROW EXECUTE FUNCTION reject_ticket_event_mutation()""")


def downgrade():
    op.execute("DROP TRIGGER ticket_state_event_immutable ON ticket_state_event")
    op.execute("DROP FUNCTION reject_ticket_event_mutation()")
    op.execute("ALTER TABLE ticket_state_event DROP COLUMN actor_id")
