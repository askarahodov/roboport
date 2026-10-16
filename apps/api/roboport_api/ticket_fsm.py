"""Server-side ticket FSM with row lock and live RBAC/ACL.

No direct PATCH of state is allowed; every transition writes an immutable event.
"""
import json
from pathlib import Path
from uuid import UUID, uuid4
import psycopg
from .authorization import authorize_ticket
from .session_lookup import session_subject

_TRANSITIONS = json.loads(
    (Path(__file__).resolve().parents[3] / "docs/domain/TICKET_TRANSITIONS.json").read_text(
        encoding="utf-8"))["transitions"]


class TransitionConflict(Exception):
    """Invalid, stale or safety-prohibited ticket transition."""


def transition_ticket(conn: psycopg.Connection, token: str, ticket_id: UUID,
                      target: str, expected_version: int) -> dict:
    """Caller owns transaction; never publish a mutation without CSRF/Origin checks."""
    if (not isinstance(ticket_id, UUID) or type(expected_version) is not int
            or expected_version < 1 or not isinstance(target, str)):
        raise TransitionConflict("invalid transition request")
    # FOR UPDATE serializes concurrent transitions against the same ticket.
    row = conn.execute("SELECT state,version FROM service_ticket WHERE id=%s FOR UPDATE",
                       (ticket_id,)).fetchone()
    if row is None:
        raise TransitionConflict("ticket unavailable")
    state, version = row
    if (version != expected_version or
            target not in _TRANSITIONS.get(state, ())):
        raise TransitionConflict("version or transition conflict")
    # SAFETY_HOLD never automatically releases; QC_FAILED cannot bypass rework.
    if state == "SAFETY_HOLD":
        raise TransitionConflict("independent safety review required")
    permission = "ticket.intake" if state in (
        "DRAFT","SUBMITTED","TRIAGE","AWAITING_DELIVERY"
    ) else "ticket.diagnose" if state in (
        "RECEIVED","DIAGNOSING","REPAIRING","QC_FAILED"
    ) else "ticket.manage"
    if not authorize_ticket(conn, token, permission, ticket_id):
        raise TransitionConflict("not permitted")
    actor = session_subject(conn, token)
    if actor is None:
        raise TransitionConflict("invalid session")
    conn.execute("UPDATE service_ticket SET state=%s,version=version+1 WHERE id=%s",
                 (target,ticket_id))
    conn.execute(
        """INSERT INTO ticket_state_event
           (id,ticket_id,from_state,to_state,expected_version,actor_id)
           VALUES (%s,%s,%s,%s,%s,%s)""",
        (uuid4(),ticket_id,state,target,expected_version,actor),
    )
    conn.execute("INSERT INTO audit_event(id,ticket_id,action) VALUES(%s,%s,%s)",
                 (uuid4(),ticket_id,"ticket.transition"))
    return {"id":str(ticket_id),"state":target,"version":version+1}



def _safety_role(conn: psycopg.Connection, actor: UUID, ticket: UUID) -> str | None:
    """Resolve live, explicitly assigned expert privilege; ADMIN is not implicit."""
    row=conn.execute(
        """SELECT r.role_code FROM user_role r
           JOIN app_user u ON u.id=r.user_id AND u.status='ACTIVE'
           JOIN acl_entry a ON a.user_id=r.user_id AND a.ticket_id=%s
             AND a.revoked_at IS NULL AND (a.expires_at IS NULL OR a.expires_at>now())
           WHERE r.user_id=%s AND r.role_code IN ('ENGINEER','MANAGER')
             AND r.revoked_at IS NULL
             AND (r.expires_at IS NULL OR r.expires_at>now())
             AND ((r.role_code='ENGINEER' AND a.action='ticket.diagnose')
               OR (r.role_code='MANAGER' AND a.action='ticket.manage'))
           ORDER BY CASE WHEN r.role_code='MANAGER' THEN 0 ELSE 1 END LIMIT 1""",(ticket,actor),
    ).fetchone()
    return row[0] if row else None


def record_safety_review(conn: psycopg.Connection, token: str,
                         ticket: UUID, expected_version: int,
                         approved: bool) -> str:
    """Append an expert decision only for the current SAFETY_HOLD version."""
    if (not isinstance(ticket,UUID) or type(expected_version) is not int
            or type(approved) is not bool):
        raise TransitionConflict("invalid safety review")
    row=conn.execute("SELECT state,version FROM service_ticket WHERE id=%s FOR UPDATE",
                     (ticket,)).fetchone()
    if row != ("SAFETY_HOLD",expected_version):
        raise TransitionConflict("safety hold version conflict")
    actor=session_subject(conn,token)
    role=_safety_role(conn,actor,ticket) if actor else None
    if not role:
        raise TransitionConflict("independent staff review required")
    conn.execute(
        """INSERT INTO ticket_safety_review
           (id,ticket_id,ticket_version,actor_id,role_code,approved)
           VALUES (%s,%s,%s,%s,%s,%s)""",
        (uuid4(),ticket,expected_version,actor,role,approved),
    )
    return role


def release_safety_hold(conn: psycopg.Connection, token: str,
                        ticket: UUID, expected_version: int) -> dict:
    """Release only after two different, still-authorized expert approvals."""
    row=conn.execute("SELECT state,version FROM service_ticket WHERE id=%s FOR UPDATE",
                     (ticket,)).fetchone()
    if row != ("SAFETY_HOLD",expected_version):
        raise TransitionConflict("safety hold version conflict")
    actor=session_subject(conn,token)
    if actor is None or _safety_role(conn,actor,ticket) != "MANAGER":
        raise TransitionConflict("manager authorization required")
    reviews=conn.execute(
        """SELECT r.actor_id,r.role_code FROM ticket_safety_review r
           JOIN app_user u ON u.id=r.actor_id AND u.status='ACTIVE'
           JOIN user_role ur ON ur.user_id=r.actor_id AND ur.role_code=r.role_code
             AND ur.revoked_at IS NULL AND (ur.expires_at IS NULL OR ur.expires_at>now())
           JOIN acl_entry a ON a.user_id=r.actor_id AND a.ticket_id=r.ticket_id
             AND a.revoked_at IS NULL AND (a.expires_at IS NULL OR a.expires_at>now())
             AND ((r.role_code='ENGINEER' AND a.action='ticket.diagnose')
               OR (r.role_code='MANAGER' AND a.action='ticket.manage'))
           WHERE r.ticket_id=%s AND r.ticket_version=%s AND r.approved=true
             AND NOT EXISTS (
               SELECT 1 FROM ticket_safety_review reject
               WHERE reject.ticket_id=r.ticket_id AND reject.ticket_version=r.ticket_version
                 AND reject.approved=false)
           """,(ticket,expected_version),
    ).fetchall()
    engineers={u for u,role in reviews if role=="ENGINEER"}
    managers={u for u,role in reviews if role=="MANAGER"}
    if not any(e != m for e in engineers for m in managers):
        raise TransitionConflict("two independent approvals required")
    conn.execute("UPDATE service_ticket SET state='DIAGNOSING',version=version+1 WHERE id=%s",
                 (ticket,))
    conn.execute(
        """INSERT INTO ticket_state_event
           (id,ticket_id,from_state,to_state,expected_version,actor_id)
           VALUES (%s,%s,'SAFETY_HOLD','DIAGNOSING',%s,%s)""",
        (uuid4(),ticket,expected_version,actor),
    )
    conn.execute("INSERT INTO audit_event(id,ticket_id,action) VALUES (%s,%s,%s)",
                 (uuid4(),ticket,"ticket.safety-release"))
    return {"id":str(ticket),"state":"DIAGNOSING","version":expected_version+1}
