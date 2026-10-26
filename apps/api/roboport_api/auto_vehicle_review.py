"""Закрытое решение мастера-приёмщика по заявленной связи авто и клиента."""
from uuid import UUID, uuid4

import psycopg

from .session_tokens import token_digest


def review_link(conn: psycopg.Connection, token: str, link_id: UUID,
                decision: str, evidence_ref: str) -> str:
    """APPROVED/REJECTED/DENIED/CONFLICT; отклонённый запрос не раскрывает объект."""
    if (not isinstance(token, str) or len(token) != 43
            or not isinstance(link_id, UUID)
            or decision not in ("APPROVE", "REJECT")
            or not isinstance(evidence_ref, str)
            or not 8 <= len(evidence_ref.strip()) <= 100
            or not evidence_ref.isascii()
            or not all(c.isalnum() or c in "_-:." for c in evidence_ref)):
        return "DENIED"
    with conn.transaction():
        # Сначала проверка субъекта и назначенной приёмки, без утечки customer/VIN.
        grant = conn.execute(
            """SELECT u.id FROM user_session s
               JOIN app_user u ON u.id=s.user_id AND u.status='ACTIVE'
               JOIN user_role ur ON ur.user_id=u.id AND ur.role_code='INTAKE'
                    AND ur.revoked_at IS NULL
                    AND (ur.expires_at IS NULL OR ur.expires_at>now())
               JOIN auto_vehicle_customer_link l ON l.id=%s AND l.revoked_at IS NULL
               JOIN service_ticket t ON t.customer_id=l.customer_id
               JOIN acl_entry a ON a.ticket_id=t.id AND a.user_id=u.id
                   AND a.action='ticket.intake' AND a.revoked_at IS NULL
                   AND (a.expires_at IS NULL OR a.expires_at>now())
               WHERE s.token_hash=%s AND s.revoked_at IS NULL
                 AND s.idle_expires_at>now() AND s.absolute_expires_at>now()
               LIMIT 1""",
            (link_id, token_digest(token)),
        ).fetchone()
        if grant is None:
            return "DENIED"
        # FOR UPDATE сериализует конкурентные решения: первая фиксация необратима.
        link = conn.execute(
            """SELECT verified_at, revoked_at FROM auto_vehicle_customer_link
               WHERE id=%s FOR UPDATE""", (link_id,),
        ).fetchone()
        if link is None or link[1] is not None:
            return "DENIED"
        if link[0] is not None or conn.execute(
            "SELECT 1 FROM auto_vehicle_link_review WHERE link_id=%s",
            (link_id,),
        ).fetchone():
            return "CONFLICT"
        conn.execute(
            """INSERT INTO auto_vehicle_link_review(
                 id,link_id,reviewer_id,decision,evidence_ref
               ) VALUES(%s,%s,%s,%s,%s)""",
            (uuid4(), link_id, grant[0], decision, evidence_ref.strip()),
        )
        if decision == "APPROVE":
            conn.execute(
                """UPDATE auto_vehicle_customer_link
                   SET verified_at=now(),verified_by=%s
                   WHERE id=%s AND verified_at IS NULL AND revoked_at IS NULL""",
                (grant[0], link_id),
            )
        else:
            conn.execute(
                """UPDATE auto_vehicle_customer_link
                   SET revoked_at=now() WHERE id=%s AND revoked_at IS NULL""",
                (link_id,),
            )
        return "APPROVED" if decision == "APPROVE" else "REJECTED"
