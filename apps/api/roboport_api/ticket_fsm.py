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
