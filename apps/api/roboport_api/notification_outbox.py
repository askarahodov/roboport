"""PostgreSQL outbox worker. No network/email/SMS operations exist here.

A claimed row is fenced by worker ID and incrementing attempt counter.
The DEV_SINK effect and DELIVERED transition commit in the same transaction.
"""
from uuid import UUID

MAX_ATTEMPTS = 5


def claim_one(conn, worker_id: str, lease_seconds: int = 30, aggregate_id: UUID | None = None):
    if (not isinstance(worker_id,str) or not 1<=len(worker_id)<=96
            or not worker_id.isascii() or not worker_id.isprintable()
            or type(lease_seconds) is not int or not 1<=lease_seconds<=3600
            or (aggregate_id is not None and not isinstance(aggregate_id,UUID))):
        raise ValueError("invalid worker/lease")

    # Reap claims that exhausted their allowance before crashing. No external effect.
    conn.execute("""UPDATE notification_outbox
      SET status='DEAD', claimed_by=NULL, claimed_at=NULL,
          last_error='lease expired after final attempt'
      WHERE status='PROCESSING' AND attempts>=5
        AND claimed_at <= clock_timestamp() - (%s * interval '1 second')""",
      (lease_seconds,))
    row=conn.execute("""WITH candidate AS (
        SELECT id FROM notification_outbox
        WHERE ((status='PENDING' AND available_at<=clock_timestamp())
           OR (status='PROCESSING' AND attempts<5 AND
               claimed_at<=clock_timestamp() - (%s * interval '1 second')))
          AND (%s::uuid IS NULL OR aggregate_id=%s::uuid)
        ORDER BY available_at,id
        FOR UPDATE SKIP LOCKED
        LIMIT 1
      )
      UPDATE notification_outbox o SET
        status='PROCESSING', attempts=o.attempts+1,
        claimed_by=%s, claimed_at=clock_timestamp()
      FROM candidate WHERE o.id=candidate.id
      RETURNING o.id,o.attempts,o.event_type,o.payload""",
      (lease_seconds,aggregate_id,aggregate_id,worker_id)).fetchone()
    if row is None:
        return None
    return {"id":row[0],"attempt":row[1],"eventType":row[2],"payload":row[3]}


def finish_one(conn, worker_id: str, event_id: UUID, attempt: int,
               *, failed: bool = False):
    if (not isinstance(worker_id,str) or not isinstance(event_id,UUID)
            or type(attempt) is not int or not 1<=attempt<=MAX_ATTEMPTS):
        raise ValueError("invalid delivery fence")
    current=conn.execute("""SELECT 1 FROM notification_outbox
      WHERE id=%s AND status='PROCESSING' AND claimed_by=%s
        AND attempts=%s FOR UPDATE""",(event_id,worker_id,attempt)).fetchone()
    if current is None:
        return False
    if failed:
        # Increasing exponential delay (2,4,8,16 seconds). On the fifth: DEAD.
        next_status='DEAD' if attempt>=MAX_ATTEMPTS else 'PENDING'
        conn.execute("""UPDATE notification_outbox SET
          status=%s, claimed_by=NULL, claimed_at=NULL,
          last_error='synthetic worker failure',
          available_at=clock_timestamp()+(%s * interval '1 second')
          WHERE id=%s""",(next_status,min(2**attempt,300),event_id))
        return True
    # Database-only observable effect. Exactly once in the disposable development sink.
    conn.execute("""INSERT INTO synthetic_notification_delivery(outbox_id,channel)
                    VALUES (%s,'DEV_SINK') ON CONFLICT(outbox_id) DO NOTHING""",
                 (event_id,))
    conn.execute("""UPDATE notification_outbox SET
      status='DELIVERED', claimed_by=NULL, claimed_at=NULL,
      delivered_at=clock_timestamp(), last_error=NULL WHERE id=%s""",
                 (event_id,))
    return True


def run_once(conn, worker_id: str, *, simulate_failure: bool=False,
             aggregate_id: UUID | None = None):
    """Caller supplies a writable transaction/connection, no external side effects."""
    task=claim_one(conn,worker_id,aggregate_id=aggregate_id)
    if task is None:
        return None
    if not finish_one(conn,worker_id,task["id"],task["attempt"],
                      failed=simulate_failure):
        raise RuntimeError("lost worker fence")
    return {"id":str(task["id"]),
            "status":"RETRY" if simulate_failure and task["attempt"]<MAX_ATTEMPTS
            else "DEAD" if simulate_failure else "DELIVERED"}


def run_batch(conn, worker_id: str, limit: int=20):
    if type(limit) is not int or not 1<=limit<=100:
        raise ValueError("invalid batch size")
    result=[]
    for _ in range(limit):
        with conn.transaction():
            item=run_once(conn,worker_id)
        if item is None:
            break
        result.append(item)
    return result
