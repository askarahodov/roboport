"""Synthetic private upload with persistent metadata and compensation on errors."""
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
    """No object is stored until live RBAC/ACL allows this particular ticket.

    The object is removed when metadata cannot commit. PENDING rows are never
    downloadable; their age can be reconciled to clean orphans.
    """
    actor=authorize_attachment_upload(conn,token,ticket_id)
    if actor is None:
        return None
    stored=storage.put(payload,mime)
    attachment_id=uuid4()
    try:
        conn.execute(
            """INSERT INTO attachment(id,ticket_id,object_key,mime,byte_size,sha256,created_by,upload_state)
               VALUES(%s,%s,%s,%s,%s,%s,%s,'READY')""",
            (attachment_id,ticket_id,stored.key,stored.mime,
             stored.size,stored.sha256,actor),
        )
    except Exception:
        storage.delete(stored.key)
        raise
    return attachment_id
