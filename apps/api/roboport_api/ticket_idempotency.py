"""Atomic idempotency receipts for synthetic ticket mutations.

Locks the ticket row to serialize retries and business transitions. Caller
must have validated session, Origin, and CSRF before invoking this function.
"""
import hashlib
import json

from .authorization import authorize_ticket
from .session_lookup import session_subject
from .ticket_fsm import TransitionConflict, _safety_role


def execute_ticket_mutation(conn, token, ticket, operation, payload, key, effect):
    if key is None:
        return effect()
    if not isinstance(key, str) or not (1 <= len(key) <= 128) or not key.isascii() or not key.isprintable():
        raise ValueError("invalid idempotency key")
    actor = session_subject(conn, token)
    if actor is None:
        raise TransitionConflict("session revoked")
    # A durable receipt must not outlive effective authorization.
    # No replay can bypass a role or ACL revocation.
    fingerprint = hashlib.sha256(json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")).hexdigest()
    row = conn.execute("SELECT id FROM service_ticket WHERE id=%s FOR UPDATE", (ticket,)).fetchone()
    if row is None:
        raise TransitionConflict("ticket unavailable")
    receipt = conn.execute(
        """SELECT fingerprint,response FROM ticket_mutation_receipt
           WHERE actor_id=%s AND ticket_id=%s AND operation=%s AND request_key=%s""",
        (actor, ticket, operation, key),
    ).fetchone()
    if receipt:
        if receipt[0] != fingerprint:
            raise TransitionConflict("idempotency key reused with different request")
        if operation == "transition":
            # The source-state permission remains the same for the recorded
            # transition; verify the live grant independently of old tokens.
            permission = receipt[1].get("_permission")
            if not permission or not authorize_ticket(conn, token, permission, ticket):
                raise TransitionConflict("permission revoked")
        else:
            role = _safety_role(conn, actor, ticket)
            if role is None or (operation == "release" and role != "MANAGER"):
                raise TransitionConflict("permission revoked")
        return {k: v for k, v in receipt[1].items() if k != "_permission"}
    # Save result in the same transaction as event and business mutation.
    source = conn.execute("SELECT state FROM service_ticket WHERE id=%s", (ticket,)).fetchone()[0]
    result = effect()
    stored = dict(result)
    if operation == "transition":
        stored["_permission"] = (
            "ticket.intake" if source in ("DRAFT","SUBMITTED","TRIAGE","AWAITING_DELIVERY")
            else "ticket.diagnose" if source in ("RECEIVED","DIAGNOSING","REPAIRING","QC_FAILED")
            else "ticket.manage"
        )
    conn.execute(
        """INSERT INTO ticket_mutation_receipt
           (actor_id,ticket_id,operation,request_key,fingerprint,response)
           VALUES (%s,%s,%s,%s,%s,%s::jsonb)""",
        (actor, ticket, operation, key, fingerprint, json.dumps(stored)),
    )
    return result
