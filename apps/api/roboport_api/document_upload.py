"""Synthetic private upload: metadata and storage cleanup on transactional failures."""
from uuid import UUID,uuid4
import psycopg
from .document_access import authorize_attachment_upload
from .storage import FileStorage


def create_attachment(
    conn: psycopg.Connection,
    storage: FileStorage,
    token: str,
    ticket_id: UUID,
    payload: bytes,
    mime: str,
) -> UUID | None:
    """Run with autocommit=True connection; use a real DB transaction per upload.

    The object is deleted on any insert or DB commit failure. After an ACL
    revoke during the slow S3 call, authorization is checked once more before
    publishing READY metadata.
    """
    stored=None
    try:
        with conn.transaction():
            actor=authorize_attachment_upload(conn,token,ticket_id)
            if actor is None:
                return None
            stored=storage.put(payload,mime)
            if authorize_attachment_upload(conn,token,ticket_id)!=actor:
                storage.delete(stored.key)
                stored=None
                return None
            attachment_id=uuid4()
            conn.execute(
                """INSERT INTO attachment(id,ticket_id,object_key,mime,byte_size,sha256,created_by,upload_state)
                   VALUES(%s,%s,%s,%s,%s,%s,%s,'READY')""",
                (attachment_id,ticket_id,stored.key,stored.mime,
                 stored.size,stored.sha256,actor),
            )
        return attachment_id
    except Exception:
        if stored is not None:
            try:
                storage.delete(stored.key)
            except Exception:
                # Reconciliation must retry removal of abandoned opaque keys.
                pass
        raise
