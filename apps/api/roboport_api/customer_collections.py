"""Synthetic-only customer collections scoped to a live session and active ownership.

No free-form IDs or externally supplied actor/customer identity are accepted.
No MinIO credentials/object keys are returned.
"""
from .session_tokens import token_digest

def owned_collection(conn, token: str, kind: str) -> list[dict] | None:
    if not isinstance(token,str) or len(token)!=43:
        return None
    if kind not in ("robots","tickets","estimates","documents"):
        raise ValueError("unsupported collection")
    tables={
        "tickets": """SELECT t.id,t.id AS ticket_id,t.state,t.version,
             NULL::bigint AS total_minor
           FROM account_customer_link l JOIN service_ticket t ON t.customer_id=l.customer_id
           WHERE l.user_id=subject.id AND l.revoked_at IS NULL
           ORDER BY t.created_at DESC,t.id DESC LIMIT 50""",
        "robots": """SELECT DISTINCT t.device_id AS id,
           NULL::uuid AS ticket_id, NULL::text AS state,
           NULL::integer AS version, NULL::bigint AS total_minor
           FROM account_customer_link l JOIN service_ticket t ON t.customer_id=l.customer_id
           WHERE l.user_id=subject.id AND l.revoked_at IS NULL AND t.device_id IS NOT NULL
           ORDER BY id LIMIT 50""",
        "estimates": """SELECT e.id,t.id AS ticket_id,e.state,e.version,e.total_minor
           FROM account_customer_link l JOIN service_ticket t ON t.customer_id=l.customer_id
             JOIN estimate e ON e.ticket_id=t.id
           WHERE l.user_id=subject.id AND l.revoked_at IS NULL
           ORDER BY e.created_at DESC,e.id DESC LIMIT 50""",
        "documents": """SELECT a.id,t.id AS ticket_id,a.upload_state AS state,
             NULL::integer AS version,NULL::bigint AS total_minor
           FROM account_customer_link l JOIN service_ticket t ON t.customer_id=l.customer_id
             JOIN attachment a ON a.ticket_id=t.id
           WHERE l.user_id=subject.id AND l.revoked_at IS NULL
             AND a.upload_state='READY' AND a.quarantine_state='CLEAR'
           ORDER BY a.created_at DESC,a.id DESC LIMIT 50""",
    }
    # LATERAL retains a row when the authenticated subject has no visible data.
    sql="""SELECT page.id,page.ticket_id,page.state,page.version,page.total_minor
      FROM user_session s
      JOIN app_user subject ON subject.id=s.user_id AND subject.status='ACTIVE'
      JOIN user_role r ON r.user_id=subject.id AND r.role_code='CLIENT'
        AND r.revoked_at IS NULL AND (r.expires_at IS NULL OR r.expires_at>now())
      LEFT JOIN LATERAL ("""+tables[kind]+""") page ON TRUE
      WHERE s.token_hash=%s AND s.revoked_at IS NULL
        AND s.idle_expires_at>now() AND s.absolute_expires_at>now()
      ORDER BY page.id NULLS LAST"""
    rows=conn.execute(sql,(token_digest(token),)).fetchall()
    if not rows:
        return None
    result=[]
    for obj,ticket,state,version,total in rows:
        if obj is None:
            continue
        item={"id":str(obj)}
        if ticket is not None:
            item["ticketId"]=str(ticket)
        if state is not None:
            item["state"]=state
        if version is not None:
            item["version"]=version
        if total is not None:
            item["totalMinor"]=total
        result.append(item)
    return result
