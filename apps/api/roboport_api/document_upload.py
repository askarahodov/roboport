"""Synthetic private upload: ACL-gated, deduplicated and compensated."""
from uuid import UUID,uuid4
import hashlib
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
    idempotency_key: str | None = None,
) -> UUID | None:
    """Atomic PG transaction, S3 compensation and live ACL on every retry."""
    if idempotency_key is not None and (not isinstance(idempotency_key,str)
                                       or not 1<=len(idempotency_key)<=128):
        raise ValueError("invalid idempotency key")
    stored=None
    try:
        with conn.transaction():
            actor=authorize_attachment_upload(conn,token,ticket_id)
            if actor is None:
                return None
            fingerprint=hashlib.sha256(payload+mime.encode("utf-8")).hexdigest()
            key_hash=(hashlib.sha256(idempotency_key.encode()).hexdigest()
                      if idempotency_key is not None else None)
            if key_hash is not None:
                # Serialize concurrent retries for one actor/ticket/key.
                conn.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                    (str(actor)+str(ticket_id)+key_hash,),
                )
                existing=conn.execute(
                    """SELECT payload_hash,attachment_id FROM attachment_upload_dedupe
                       WHERE actor_id=%s AND ticket_id=%s AND key_hash=%s""",
                    (actor,ticket_id,key_hash),
                ).fetchone()
                if existing:
                    if existing[0]!=fingerprint:
                        raise ValueError("idempotency payload conflict")
                    return existing[1]
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
            if key_hash is not None:
                conn.execute(
                    """INSERT INTO attachment_upload_dedupe(actor_id,ticket_id,key_hash,payload_hash,attachment_id)
                       VALUES(%s,%s,%s,%s,%s)""",
                    (actor,ticket_id,key_hash,fingerprint,attachment_id),
                )
        return attachment_id
    except Exception:
        if stored is not None:
            try:
                storage.delete(stored.key)
            except Exception:
                # Aged orphan reconciliation cleans abandoned synthetic keys.
                pass
        raise
