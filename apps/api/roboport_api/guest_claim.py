"""Verified guest claim workflow. No HTTP-supplied customer/user identity is trusted.

A staff member explicitly issues a 256-bit proof through a separate trusted
channel; only an authenticated client may redeem it. A different MANAGER
reviews it; only then does a customer link become active.
"""
from datetime import timedelta
from uuid import UUID, uuid4
import hashlib
import hmac
import secrets
import psycopg
from .session_lookup import session_subject
from .session_tokens import token_digest

class ClaimRejected(Exception):
    pass

def _staff(conn:psycopg.Connection, token:str, role:str, ticket:UUID|None=None)->UUID:
    actor=session_subject(conn,token)
    if actor is None:
        raise ClaimRejected("unauthorized")
    row=conn.execute("""
      SELECT 1 FROM user_role ur WHERE ur.user_id=%s AND ur.role_code=%s
        AND ur.revoked_at IS NULL AND (ur.expires_at IS NULL OR ur.expires_at>now())
        AND (%s::uuid IS NULL OR EXISTS (
          SELECT 1 FROM acl_entry a WHERE a.user_id=ur.user_id AND a.ticket_id=%s
            AND a.action=CASE WHEN %s='INTAKE' THEN 'ticket.intake' ELSE 'ticket.manage' END
            AND a.revoked_at IS NULL AND (a.expires_at IS NULL OR a.expires_at>now())))
      LIMIT 1
    """,(actor,role,ticket,ticket,role)).fetchone()
    if not row:
        raise ClaimRejected("forbidden")
    return actor

def issue_proof(conn:psycopg.Connection, staff_token:str, ticket:UUID)->tuple[UUID,str]:
    """Assigned INTAKE staff only; issued raw proof must never be logged."""
    _staff(conn,staff_token,"INTAKE",ticket)
    ticket_row=conn.execute("SELECT customer_id FROM service_ticket WHERE id=%s FOR UPDATE",(ticket,)).fetchone()
    if not ticket_row:
        raise ClaimRejected("unavailable")
    raw=secrets.token_urlsafe(32)
    ident=uuid4()
    conn.execute("""INSERT INTO guest_ticket_claim(id,ticket_id,customer_id,proof_hash,proof_expires_at)
        VALUES (%s,%s,%s,%s,now()+interval '20 minutes')""",
        (ident,ticket,ticket_row[0],hashlib.sha256(raw.encode()).hexdigest()))
    conn.execute("""INSERT INTO guest_claim_audit(id,claim_id,actor_id,action)
        VALUES(%s,%s,%s,'INTAKE')""",(uuid4(),ident,session_subject(conn,staff_token)))
    return ident,raw

def redeem_proof(conn:psycopg.Connection, client_token:str, claim:UUID, raw:str)->str:
    actor=session_subject(conn,client_token)
    if actor is None:
        raise ClaimRejected("unauthorized")
    role=conn.execute("""SELECT 1 FROM user_role WHERE user_id=%s AND role_code='CLIENT'
        AND revoked_at IS NULL AND (expires_at IS NULL OR expires_at>now())""",(actor,)).fetchone()
    if not role:
        raise ClaimRejected("forbidden")
    if not isinstance(raw,str) or not 20<=len(raw)<=200:
        raise ClaimRejected("invalid proof")
    row=conn.execute("""SELECT requested_user_id,proof_hash,proof_attempts,proof_expires_at,
        proof_verified_at,status FROM guest_ticket_claim WHERE id=%s FOR UPDATE""",(claim,)).fetchone()
    if row is None or row[5]!='PENDING_REVIEW' or row[4] is not None or row[0] is not None:
        raise ClaimRejected("unavailable")
    if row[2]>=5:
        raise ClaimRejected("locked")
    # No changes after TTL expiry, including old-token replay.
    from datetime import datetime,timezone
    if row[3]<=datetime.now(timezone.utc):
        raise ClaimRejected("expired")
    valid=hmac.compare_digest(hashlib.sha256(raw.encode()).hexdigest(),row[1])
    conn.execute("""UPDATE guest_ticket_claim SET proof_attempts=proof_attempts+1,
         requested_user_id=CASE WHEN %s THEN %s ELSE NULL END,
         proof_verified_at=CASE WHEN %s THEN now() ELSE NULL END
         WHERE id=%s""",(valid,actor,valid,claim))
    conn.execute("""INSERT INTO guest_claim_audit(id,claim_id,actor_id,action)
         VALUES (%s,%s,%s,%s)""",(uuid4(),claim,actor,'PROOF_ACCEPTED' if valid else 'PROOF_REJECTED'))
    if not valid:
        return "REJECTED"
    return "PENDING_REVIEW"

def review_claim(conn:psycopg.Connection,manager_token:str,claim:UUID,approve:bool)->str:
    if type(approve) is not bool:
        raise ClaimRejected("invalid decision")
    row=conn.execute("""SELECT ticket_id,customer_id,requested_user_id,proof_verified_at,status
        FROM guest_ticket_claim WHERE id=%s FOR UPDATE""",(claim,)).fetchone()
    if row is None or row[4]!='PENDING_REVIEW' or row[3] is None or row[2] is None:
        raise ClaimRejected("claim not verified")
    manager=_staff(conn,manager_token,"MANAGER",row[0])
    if manager==row[2]:
        raise ClaimRejected("independent reviewer required")
    status="VERIFIED" if approve else "DECLINED"
    conn.execute("""UPDATE guest_ticket_claim
       SET status=%s,reviewed_by=%s,reviewed_at=now() WHERE id=%s""",(status,manager,claim))
    if approve:
        # Claim uses its own ticket's claimant, never the device's latest owner.
        conn.execute("""INSERT INTO account_customer_link(user_id,customer_id)
          VALUES(%s,%s) ON CONFLICT(user_id,customer_id) DO NOTHING""",(row[2],row[1]))
        # No silent un-revoke: an existing revoked link stays revoked.
    conn.execute("""INSERT INTO guest_claim_audit(id,claim_id,actor_id,action)
         VALUES(%s,%s,%s,%s)""",(uuid4(),claim,manager,status))
    return status

def assign_review(conn:psycopg.Connection,manager_token:str,claim:UUID)->UUID:
    """Explicit MANAGER action claims a proof-verified ticket for review."""
    manager=_staff(conn,manager_token,"MANAGER")
    row=conn.execute("""SELECT ticket_id,requested_user_id,proof_verified_at,status
      FROM guest_ticket_claim WHERE id=%s FOR UPDATE""",(claim,)).fetchone()
    if row is None or row[3]!='PENDING_REVIEW' or row[2] is None or row[1] is None:
        raise ClaimRejected("unavailable")
    if manager==row[1]:
        raise ClaimRejected("independent reviewer required")
    # Exactly one assigned, live review manager; subsequent attempts cannot
    # silently shift review ownership.
    existing=conn.execute("""SELECT user_id FROM acl_entry WHERE ticket_id=%s
        AND action='ticket.manage' AND revoked_at IS NULL
        AND (expires_at IS NULL OR expires_at>now()) FOR UPDATE""",(row[0],)).fetchall()
    if existing:
        if any(r[0]==manager for r in existing):
            return row[0]
        raise ClaimRejected("review already assigned")
    conn.execute("""INSERT INTO acl_entry(id,user_id,ticket_id,action)
       VALUES(%s,%s,%s,'ticket.manage')""",(uuid4(),manager,row[0]))
    return row[0]
