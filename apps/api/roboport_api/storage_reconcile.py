"""Reconcile orphaned synthetic files in the private documents bucket.

Only call from trusted maintenance context with MinIO app credentials and DB
connection. Never expose via HTTP. Recent writes have a 1-hour grace period.
"""
from datetime import datetime, timedelta, timezone
import psycopg
from .storage import DOCUMENTS,MinioFileStorage


def purge_old_orphans(conn: psycopg.Connection, storage: MinioFileStorage) -> int:
    cutoff=datetime.now(timezone.utc)-timedelta(hours=1)
    purged=0
    for obj in storage._client.list_objects(DOCUMENTS,recursive=True):
        if obj.last_modified is None or obj.last_modified >= cutoff:
            continue
        key=obj.object_name
        row=conn.execute(
            "SELECT 1 FROM attachment WHERE object_key=%s", (key,)
        ).fetchone()
        if row is None:
            storage.delete(key)
            purged+=1
    return purged
