"""Batch cleanup of *expired* synthetic proof throttle counters.

Only the mutable auto_proof_throttle table is touched; immutable receipts, proof
hashes and append-only audit must never be removed by this maintenance routine.
A fixed 24-hour grace period exceeds the longest ISSUE window (60 minutes).
"""
from collections import Counter

import psycopg


RETENTION_HOURS = 24
MAX_BATCH = 1000


def expired_counts(conn: psycopg.Connection) -> dict[str, int]:
    """Return aggregate counts only: never return actor identifiers."""
    return dict(conn.execute(
        """SELECT operation, count(*) FROM auto_proof_throttle
           WHERE window_started_at < now() - interval '24 hours'
           GROUP BY operation ORDER BY operation"""
    ).fetchall())


def prune_expired(conn: psycopg.Connection, *, batch_size: int = 100) -> dict[str, int]:
    """Remove at most one bounded batch, skipping counters used by requests.

    The locked selection is re-checked at READ COMMITTED when a concurrent
    UPSERT has updated the row; SKIP LOCKED avoids blocking live rate-limits.
    The caller owns the transaction and must commit explicitly/on context exit.
    """
    if type(batch_size) is not int or not 1 <= batch_size <= MAX_BATCH:
        raise ValueError("batch_size must be between 1 and 1000")
    deleted = conn.execute(
        """WITH stale AS (
             SELECT actor_id, operation
             FROM auto_proof_throttle
             WHERE window_started_at < now() - interval '24 hours'
             ORDER BY window_started_at,actor_id,operation
             LIMIT %s FOR UPDATE SKIP LOCKED
           )
           DELETE FROM auto_proof_throttle t USING stale s
           WHERE t.actor_id=s.actor_id AND t.operation=s.operation
           RETURNING t.operation""",
        (batch_size,),
    ).fetchall()
    return dict(Counter(row[0] for row in deleted))
