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


def create_estimate_version(conn, token: str, ticket_id: UUID,
                            lines: list[dict], currency: str = "RUB") -> dict:
    """Manager creates a new DRAFT version; price is computed in minor units."""
    from .authorization import authorize_estimate
    if (not isinstance(ticket_id, UUID) or not isinstance(lines,list)
            or not lines or len(lines)>100 or currency!="RUB"):
        raise ValueError("invalid draft")
    amounts=[]
    for line in lines:
        if (not isinstance(line,dict) or not isinstance(line.get("description"),str)
                or not line["description"].strip() or len(line["description"])>500
                or type(line.get("amountMinor")) is not int
                or not 0 <= line["amountMinor"] <= 10**12):
            raise ValueError("invalid line")
        amounts.append(line["amountMinor"])
    actor=session_subject(conn,token)
    if actor is None:
        raise EstimateForbidden()
    conn.execute("SELECT id FROM service_ticket WHERE id=%s FOR UPDATE",(ticket_id,))
    manager=conn.execute("""SELECT EXISTS(
       SELECT 1 FROM user_role r
       JOIN acl_entry a ON a.user_id=r.user_id AND a.ticket_id=%s
           AND a.action='estimate.approve'
           AND a.revoked_at IS NULL AND (a.expires_at IS NULL OR a.expires_at>now())
       WHERE r.user_id=%s AND r.role_code='MANAGER' AND r.revoked_at IS NULL
         AND (r.expires_at IS NULL OR r.expires_at>now())
    )""",(ticket_id,actor)).fetchone()[0]
    if not manager:
        raise EstimateForbidden()
    last=conn.execute("SELECT version,state FROM estimate WHERE ticket_id=%s ORDER BY version DESC LIMIT 1",
                      (ticket_id,)).fetchone()
    if last is not None and last[1] not in ("SENT","DRAFT"):
        raise EstimateConflict("decision finalized")
    if last is not None and last[1]=="DRAFT":
        raise EstimateConflict("finish existing draft first")
    version=1 if last is None else last[0]+1
    eid=uuid4()
    total=sum(amounts)
    conn.execute("""INSERT INTO estimate(id,ticket_id,version,state,total_minor,currency)
       VALUES(%s,%s,%s,'DRAFT',%s,%s)""",(eid,ticket_id,version,total,currency))
    for line in lines:
        conn.execute("""INSERT INTO estimate_line(id,estimate_id,description,amount_minor)
           VALUES(%s,%s,%s,%s)""",(uuid4(),eid,line["description"].strip(),line["amountMinor"]))
    return {"id":str(eid),"version":version,"state":"DRAFT","totalMinor":total}


def send_estimate_version(conn, token: str, eid: UUID, expected_version: int) -> dict:
    """One-way DRAFT->SENT, exact version and computed line total."""
    actor=session_subject(conn,token)
    if actor is None:
        raise EstimateForbidden()
    row=conn.execute("SELECT ticket_id FROM estimate WHERE id=%s",(eid,)).fetchone()
    if row is None:
        raise EstimateForbidden()
    ticket=row[0]
    conn.execute("SELECT id FROM service_ticket WHERE id=%s FOR UPDATE",(ticket,))
    allowed=conn.execute("""SELECT EXISTS(
      SELECT 1 FROM user_role r JOIN acl_entry a ON a.user_id=r.user_id
        AND a.ticket_id=%s AND a.action='estimate.approve'
        AND a.revoked_at IS NULL AND (a.expires_at IS NULL OR a.expires_at>now())
      WHERE r.user_id=%s AND r.role_code='MANAGER' AND r.revoked_at IS NULL
        AND (r.expires_at IS NULL OR r.expires_at>now())
    )""",(ticket,actor)).fetchone()[0]
    if not allowed:
        raise EstimateForbidden()
    row=conn.execute("SELECT state,version,total_minor FROM estimate WHERE id=%s FOR UPDATE",
                     (eid,)).fetchone()
    if row is None or row[0]!="DRAFT" or row[1]!=expected_version:
        raise EstimateConflict("stale or already sent")
    actual=conn.execute("SELECT coalesce(sum(amount_minor),0),count(*) FROM estimate_line WHERE estimate_id=%s",
                        (eid,)).fetchone()
    if actual[1]==0 or actual[0]!=row[2]:
        raise EstimateConflict("line sum mismatch")
    latest=conn.execute("SELECT max(version) FROM estimate WHERE ticket_id=%s",(ticket,)).fetchone()[0]
    if latest!=expected_version:
        raise EstimateConflict("stale version")
    conn.execute("UPDATE estimate SET state='SENT' WHERE id=%s",(eid,))
    return {"id":str(eid),"version":expected_version,"state":"SENT","totalMinor":row[2]}
