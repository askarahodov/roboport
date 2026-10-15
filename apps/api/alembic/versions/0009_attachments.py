"""Private attachment metadata for ticket-bound documents.

Revision ID: 0009_attachments
Revises: 0008_authorization
"""
from alembic import op

revision = "0009_attachments"
down_revision = "0008_authorization"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
      CREATE TABLE attachment (
        id uuid PRIMARY KEY,
        ticket_id uuid NOT NULL REFERENCES service_ticket(id) ON DELETE RESTRICT,
        object_key char(32) NOT NULL UNIQUE CHECK (object_key ~ '^[0-9a-f]{32}$'),
        mime text NOT NULL CHECK (mime IN ('application/pdf','image/png','image/jpeg','text/plain')),
        byte_size integer NOT NULL CHECK (byte_size BETWEEN 1 AND 8388608),
        sha256 char(64) NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
        upload_state text NOT NULL DEFAULT 'PENDING'
          CHECK (upload_state IN ('PENDING','READY','FAILED','DELETED')),
        quarantine_state text NOT NULL DEFAULT 'CLEAR'
          CHECK (quarantine_state IN ('CLEAR','QUARANTINED')),
        created_by uuid NOT NULL REFERENCES app_user(id) ON DELETE RESTRICT,
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now()
      )
    """)
    op.execute("CREATE INDEX ix_attachment_ticket ON attachment(ticket_id)")
    op.execute("""
      CREATE FUNCTION guard_attachment_identity() RETURNS trigger LANGUAGE plpgsql AS $body$
      BEGIN
        IF (NEW.id,NEW.ticket_id,NEW.object_key,NEW.created_by,NEW.created_at,
            NEW.sha256,NEW.mime,NEW.byte_size)
           IS DISTINCT FROM
           (OLD.id,OLD.ticket_id,OLD.object_key,OLD.created_by,OLD.created_at,
            OLD.sha256,OLD.mime,OLD.byte_size) THEN
          RAISE EXCEPTION 'attachment identity immutable';
        END IF;
        IF OLD.upload_state IN ('FAILED','DELETED') AND
           NEW.upload_state IS DISTINCT FROM OLD.upload_state THEN
          RAISE EXCEPTION 'terminal attachment state immutable';
        END IF;
        IF OLD.quarantine_state='QUARANTINED' AND
           NEW.quarantine_state IS DISTINCT FROM OLD.quarantine_state THEN
          RAISE EXCEPTION 'quarantine irreversible';
        END IF;
        IF NEW.upload_state IS DISTINCT FROM OLD.upload_state AND NOT (
            OLD.upload_state='PENDING' AND
            NEW.upload_state IN ('READY','FAILED','DELETED')
            OR OLD.upload_state='READY' AND NEW.upload_state IN ('DELETED','FAILED')
        ) THEN
          RAISE EXCEPTION 'invalid attachment state change';
        END IF;
        NEW.updated_at=now();
        RETURN NEW;
      END $body$;
    """)
    op.execute("""CREATE TRIGGER attachment_identity_guard BEFORE UPDATE ON attachment
                  FOR EACH ROW EXECUTE FUNCTION guard_attachment_identity()""")


def downgrade():
    op.execute("DROP TABLE attachment")
    op.execute("DROP FUNCTION guard_attachment_identity()")
