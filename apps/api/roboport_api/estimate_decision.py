"""Атомарное согласие владельца с точной версией SENT-сметы."""
import hashlib
import json
from uuid import UUID, uuid4

from .session_lookup import session_subject


class EstimateConflict(Exception):
    pass


class EstimateForbidden(Exception):
    pass


def decide_estimate(conn, token: str, estimate_id: UUID, expected_version: int,
                    decision: str, key: str) -> dict:
    if (not isinstance(estimate_id, UUID) or type(expected_version) is not int
            or expected_version < 1 or decision not in ("APPROVED", "DECLINED")
            or not isinstance(key, str) or not 1 <= len(key) <= 128
            or not key.isascii() or not key.isprintable()):
        raise ValueError("invalid estimate decision")
    actor = session_subject(conn, token)
    if actor is None:
        raise EstimateForbidden()
    # Parent row lock is shared with the SQL estimate-version guard.
    header = conn.execute("SELECT ticket_id FROM estimate WHERE id=%s", (estimate_id,)).fetchone()
    if header is None:
        raise EstimateForbidden()
    ticket_id = header[0]
    conn.execute("SELECT id FROM service_ticket WHERE id=%s FOR UPDATE", (ticket_id,))
    # Authorize on every request, including completed idempotency replays.
    eligible = conn.execute(
        """SELECT EXISTS (
          SELECT 1 FROM estimate e
          JOIN service_ticket t ON t.id=e.ticket_id
          JOIN account_customer_link l ON l.customer_id=t.customer_id
            AND l.user_id=%s AND l.revoked_at IS NULL
          JOIN user_role r ON r.user_id=l.user_id AND r.role_code='CLIENT'
            AND r.revoked_at IS NULL AND (r.expires_at IS NULL OR r.expires_at>now())
          WHERE e.id=%s
        )""", (actor, estimate_id),
    ).fetchone()[0]
    if not eligible:
        raise EstimateForbidden()
    canonical = json.dumps({"estimateId":str(estimate_id),"expectedVersion":expected_version,
                            "decision":decision}, sort_keys=True, separators=(",",":"))
    fingerprint = hashlib.sha256(canonical.encode()).hexdigest()
    stored = conn.execute(
        """SELECT fingerprint, response FROM estimate_decision_receipt
           WHERE actor_id=%s AND estimate_id=%s AND request_key=%s""",
        (actor, estimate_id, key),
    ).fetchone()
    if stored:
        if stored[0] != fingerprint:
            raise EstimateConflict("key fingerprint conflict")
        return stored[1]
    state_version = conn.execute(
        "SELECT state,version FROM estimate WHERE id=%s FOR UPDATE", (estimate_id,),
    ).fetchone()
    if state_version != ("SENT",expected_version):
        raise EstimateConflict("not current SENT estimate")
    latest = conn.execute(
        "SELECT max(version) FROM estimate WHERE ticket_id=%s", (ticket_id,),
    ).fetchone()[0]
    if latest != expected_version:
        raise EstimateConflict("stale estimate")
    conn.execute(
        """INSERT INTO estimate_approval(id,estimate_id,actor_id,decision)
           VALUES (%s,%s,%s,%s)""", (uuid4(),estimate_id,actor,decision),
    )
    response = {"id":str(estimate_id),"version":expected_version,"state":decision}
    conn.execute(
        """INSERT INTO estimate_decision_receipt
           (actor_id,estimate_id,request_key,fingerprint,response)
           VALUES (%s,%s,%s,%s,%s::jsonb)""",
        (actor, estimate_id, key, fingerprint, json.dumps(response)),
    )
    return response
