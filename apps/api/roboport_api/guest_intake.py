"""Anonymous synthetic intake: receipt only, no view permissions or claim issuance."""
from uuid import UUID,uuid4
import hashlib
import secrets
import psycopg

class IntakeLimited(Exception): pass

def create_guest_request(conn:psycopg.Connection,request_key:str,origin_key:str)->tuple[str,str]:
    if (not isinstance(request_key,str) or not 16<=len(request_key)<=128
        or not isinstance(origin_key,str) or len(origin_key)<3):
        raise ValueError("invalid idempotency")
    digest=hashlib.sha256((origin_key+":"+request_key).encode()).hexdigest()
    # Serialize simultaneous retries of the same key without locking unrelated requests.
    conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",(digest,))
    existing=conn.execute("SELECT id FROM guest_intake_request WHERE request_key_hash=%s",(digest,)).fetchone()
    if existing:
        return str(existing[0]),"PENDING_REVIEW"
    origin_hash=hashlib.sha256(origin_key.encode()).hexdigest()
    count=conn.execute("""
       INSERT INTO guest_intake_rate(origin_hash,attempts) VALUES(%s,1)
       ON CONFLICT (origin_hash) DO UPDATE SET
         attempts=CASE WHEN guest_intake_rate.window_started_at<now()-interval '1 hour'
            THEN 1 ELSE guest_intake_rate.attempts+1 END,
         window_started_at=CASE WHEN guest_intake_rate.window_started_at<now()-interval '1 hour'
            THEN now() ELSE guest_intake_rate.window_started_at END
       RETURNING attempts
    """,(origin_hash,)).fetchone()[0]
    if count>10: raise IntakeLimited("rate limit")
    cust,ticket,ident=uuid4(),uuid4(),uuid4()
    conn.execute("INSERT INTO customer(id,label) VALUES(%s,'Синтетический гость')",(cust,))
    conn.execute("INSERT INTO service_ticket(id,customer_id,state) VALUES(%s,%s,'DRAFT')",(ticket,cust))
    receipt=secrets.token_urlsafe(32)
    conn.execute("""INSERT INTO guest_intake_request
      (id,ticket_id,request_key_hash,receipt_hash)
      VALUES(%s,%s,%s,%s)""",(ident,ticket,digest,hashlib.sha256(receipt.encode()).hexdigest()))
    # Receipt must not grant ticket lookup; ID returned for de-duplicating local submission only.
    return str(ident),"PENDING_REVIEW"

def assign_intake(conn:psycopg.Connection,staff_token:str,request_id:UUID)->UUID:
    from .session_lookup import session_subject
    actor=session_subject(conn,staff_token)
    if actor is None:raise IntakeLimited("unauthorized")
    assigned=conn.execute("""SELECT 1 FROM user_role
        WHERE user_id=%s AND role_code='INTAKE' AND revoked_at IS NULL
        AND (expires_at IS NULL OR expires_at>now())""",(actor,)).fetchone()
    if not assigned:raise IntakeLimited("forbidden")
    row=conn.execute("""SELECT ticket_id FROM guest_intake_request
       WHERE id=%s AND status='PENDING_REVIEW' FOR UPDATE""",(request_id,)).fetchone()
    if row is None:raise IntakeLimited("unavailable")
    conn.execute("UPDATE guest_intake_request SET status='CLAIMED',claimed_by=%s WHERE id=%s",(actor,request_id))
    conn.execute("""INSERT INTO acl_entry(id,user_id,ticket_id,action)
       VALUES(%s,%s,%s,'ticket.intake')""",(uuid4(),actor,row[0]))
    return row[0]
