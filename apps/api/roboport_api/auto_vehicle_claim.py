"""Доказательство владения авто и независимое подтверждение сотрудником.

Only synthetic data: доказательство выдаётся по закрытому каналу, сырое значение
не сохраняется в БД и не попадает в audit. Это не подтверждает старые наряды.
"""
from datetime import datetime, timezone
import hashlib
import hmac
import secrets
from uuid import UUID, uuid4

import psycopg

from .session_lookup import session_subject


class VehicleClaimRejected(Exception):
    """Не раскрывать вызывающему причины неуспешного подтверждения."""


def _role_scoped(
    conn: psycopg.Connection, token: str, role: str, vehicle: UUID,
) -> UUID:
    """Действующая сессия + роль + назначение на эту конкретную машину."""
    actor = session_subject(conn, token)
    if actor is None:
        raise VehicleClaimRejected("unauthorized")
    granted = conn.execute(
        """SELECT 1 FROM user_role ur
           JOIN auto_vehicle_staff_assignment a ON a.user_id=ur.user_id
             AND a.vehicle_id=%s AND a.role_code=%s AND a.revoked_at IS NULL
           WHERE ur.user_id=%s AND ur.role_code=%s
             AND ur.revoked_at IS NULL
             AND (ur.expires_at IS NULL OR ur.expires_at>now())
           LIMIT 1""",
        (vehicle, role, actor, role),
    ).fetchone()
    if granted is None:
        raise VehicleClaimRejected("scope denied")
    return actor


def _audit(
    conn: psycopg.Connection, *, link: UUID, claim: UUID | None,
    actor: UUID, action: str,
) -> None:
    conn.execute(
        """INSERT INTO auto_vehicle_claim_audit(id,link_id,claim_id,actor_id,action)
           VALUES(%s,%s,%s,%s,%s)""",
        (uuid4(), link, claim, actor, action),
    )


def issue_auto_proof(
    conn: psycopg.Connection, staff_token: str, link_id: UUID,
) -> tuple[UUID, str]:
    """Только назначенный INTAKE; одноразовый секрет 256 бит, TTL 20 мин."""
    link = conn.execute(
        """SELECT vehicle_id,verified_at,revoked_at FROM auto_vehicle_customer_link
           WHERE id=%s FOR UPDATE""", (link_id,),
    ).fetchone()
    if link is None:
        raise VehicleClaimRejected("unavailable")
    issuer = _role_scoped(conn, staff_token, "INTAKE", link[0])
    if link[1] is not None or link[2] is not None:
        raise VehicleClaimRejected("link not pending")
    if conn.execute(
        "SELECT 1 FROM auto_vehicle_claim WHERE link_id=%s", (link_id,),
    ).fetchone():
        raise VehicleClaimRejected("claim already exists")
    proof = secrets.token_urlsafe(32)
    claim_id = uuid4()
    conn.execute(
        """INSERT INTO auto_vehicle_claim
           (id,link_id,issued_by,proof_hash,proof_expires_at)
           VALUES(%s,%s,%s,%s,now()+interval '20 minutes')""",
        (claim_id, link_id, issuer, hashlib.sha256(proof.encode()).hexdigest()),
    )
    _audit(conn, link=link_id, claim=claim_id, actor=issuer, action="ISSUED")
    return claim_id, proof


def redeem_auto_proof(
    conn: psycopg.Connection, client_token: str, claim_id: UUID, proof: str,
) -> str:
    actor = session_subject(conn, client_token)
    if actor is None:
        raise VehicleClaimRejected("unauthorized")
    if not isinstance(proof, str) or not 20 <= len(proof) <= 200:
        raise VehicleClaimRejected("invalid proof")
    row = conn.execute(
        """SELECT c.link_id,l.customer_id,l.revoked_at,l.verified_at,
                  c.proof_hash,c.proof_expires_at,c.proof_attempts,
                  c.claimant_id,c.status
           FROM auto_vehicle_claim c
           JOIN auto_vehicle_customer_link l ON l.id=c.link_id
           WHERE c.id=%s FOR UPDATE OF c,l""",
        (claim_id,),
    ).fetchone()
    if (row is None or row[2] is not None or row[3] is not None
            or row[7] is not None or row[8] != "PENDING"
            or row[6] >= 5 or row[5] <= datetime.now(timezone.utc)):
        raise VehicleClaimRejected("unavailable")
    eligible = conn.execute(
        """SELECT 1 FROM account_customer_link l
           JOIN user_role ur ON ur.user_id=l.user_id AND ur.role_code='CLIENT'
             AND ur.revoked_at IS NULL
             AND (ur.expires_at IS NULL OR ur.expires_at>now())
           WHERE l.user_id=%s AND l.customer_id=%s AND l.revoked_at IS NULL""",
        (actor, row[1]),
    ).fetchone()
    if eligible is None:
        raise VehicleClaimRejected("customer not linked")
    valid = hmac.compare_digest(
        hashlib.sha256(proof.encode()).hexdigest(), row[4],
    )
    conn.execute(
        """UPDATE auto_vehicle_claim
           SET proof_attempts=proof_attempts+1,
               claimant_id=CASE WHEN %s THEN %s ELSE NULL END,
               claimed_at=CASE WHEN %s THEN now() ELSE NULL END
           WHERE id=%s""",
        (valid, actor, valid, claim_id),
    )
    _audit(conn, link=row[0], claim=claim_id, actor=actor,
           action="PROOF_ACCEPTED" if valid else "PROOF_REJECTED")
    return "PENDING_REVIEW" if valid else "REJECTED"


def review_auto_claim(
    conn: psycopg.Connection, manager_token: str, claim_id: UUID, approve: bool,
) -> str:
    if type(approve) is not bool:
        raise VehicleClaimRejected("invalid decision")
    row = conn.execute(
        """SELECT c.link_id,c.issued_by,c.claimant_id,c.claimed_at,c.status,
                  l.vehicle_id,l.customer_id,l.revoked_at,l.verified_at
           FROM auto_vehicle_claim c
           JOIN auto_vehicle_customer_link l ON l.id=c.link_id
           WHERE c.id=%s FOR UPDATE OF c,l""",
        (claim_id,),
    ).fetchone()
    if (row is None or row[4] != "PENDING" or row[2] is None
            or row[3] is None or row[7] is not None or row[8] is not None):
        raise VehicleClaimRejected("unavailable")
    reviewer = _role_scoped(conn, manager_token, "MANAGER", row[5])
    if reviewer in (row[1], row[2]):
        raise VehicleClaimRejected("independent reviewer required")
    claimant_valid = conn.execute(
        """SELECT 1 FROM app_user u
           JOIN user_role ur ON ur.user_id=u.id AND ur.role_code='CLIENT'
              AND ur.revoked_at IS NULL
              AND (ur.expires_at IS NULL OR ur.expires_at>now())
           JOIN account_customer_link l ON l.user_id=u.id AND l.customer_id=%s
             AND l.revoked_at IS NULL
           WHERE u.id=%s AND u.status='ACTIVE'""",
        (row[6], row[2]),
    ).fetchone()
    if claimant_valid is None:
        raise VehicleClaimRejected("claimant access revoked")
    outcome = "VERIFIED" if approve else "DECLINED"
    conn.execute(
        """UPDATE auto_vehicle_claim
           SET reviewed_by=%s,reviewed_at=now(),status=%s WHERE id=%s""",
        (reviewer, outcome, claim_id),
    )
    if approve:
        conn.execute(
            """UPDATE auto_vehicle_customer_link
               SET verified_at=now(),verified_by=%s
               WHERE id=%s AND verified_at IS NULL AND revoked_at IS NULL""",
            (reviewer, row[0]),
        )
    _audit(conn, link=row[0], claim=claim_id, actor=reviewer, action=outcome)
    return outcome


def revoke_auto_link(
    conn: psycopg.Connection, manager_token: str, link_id: UUID,
) -> str:
    row = conn.execute(
        "SELECT vehicle_id,revoked_at FROM auto_vehicle_customer_link WHERE id=%s FOR UPDATE",
        (link_id,),
    ).fetchone()
    if row is None:
        raise VehicleClaimRejected("unavailable")
    reviewer = _role_scoped(conn, manager_token, "MANAGER", row[0])
    if row[1] is None:
        conn.execute(
            "UPDATE auto_vehicle_customer_link SET revoked_at=now() WHERE id=%s",
            (link_id,),
        )
        linked_claim = conn.execute(
            "SELECT id FROM auto_vehicle_claim WHERE link_id=%s",
            (link_id,),
        ).fetchone()
        _audit(conn, link=link_id, claim=linked_claim[0] if linked_claim else None,
               actor=reviewer, action="REVOKED")
    return "REVOKED"
