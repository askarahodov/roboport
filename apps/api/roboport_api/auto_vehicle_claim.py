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
from .auto_location_scope import location_scope_allows
from .auto_proof_throttle import consume_attempt
from .auto_claim_retry import lookup as retry_lookup, remember as retry_remember, AutoClaimRetryConflict


class VehicleClaimRejected(Exception):
    """Не раскрывать вызывающему причины неуспешного подтверждения."""


def _role_scoped(
    conn: psycopg.Connection, token: str, role: str, vehicle: UUID,
) -> UUID:
    """Действующая сессия + роль + назначение на эту конкретную машину."""
    actor = session_subject(conn, token, for_mutation=True)
    if actor is None:
        raise VehicleClaimRejected("unauthorized")
    # Сериализация успешной авторизации с отзывом роли/назначения:
    # concurrent UPDATE revoked_at ждёт завершения защищённой операции.
    granted = conn.execute(
        """SELECT 1 FROM user_role ur
           JOIN auto_vehicle_staff_assignment a ON a.user_id=ur.user_id
             AND a.vehicle_id=%s AND a.role_code=%s AND a.revoked_at IS NULL
           WHERE ur.user_id=%s AND ur.role_code=%s
             AND ur.revoked_at IS NULL
             AND (ur.expires_at IS NULL OR ur.expires_at>now())
           LIMIT 1 FOR SHARE OF ur,a""",
        (vehicle, role, actor, role),
    ).fetchone()
    if granted is None or not location_scope_allows(conn, actor, role, vehicle):
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
    # Бюджет действует на выдающего INTAKE по всем авто, не на link UUID.
    consume_attempt(conn, issuer, "ISSUE")
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


def reissue_auto_proof(
    conn: psycopg.Connection, staff_token: str, link_id: UUID,
) -> tuple[UUID, str]:
    """Ровно одна замена proof для link без попыток и claimant.

    Нельзя восстанавливать старый секрет: создаётся новый, старый перестаёт
    быть допустимым при любом предъявлении. Исходный claim неизменяем.
    """
    row = conn.execute(
        """SELECT c.id,c.issued_by,c.proof_attempts,c.claimant_id,c.status,
                  l.vehicle_id,l.verified_at,l.revoked_at
           FROM auto_vehicle_claim c
           JOIN auto_vehicle_customer_link l ON l.id=c.link_id
           WHERE c.link_id=%s FOR UPDATE OF c,l""",
        (link_id,),
    ).fetchone()
    if row is None:
        raise VehicleClaimRejected("unavailable")
    issuer = _role_scoped(conn, staff_token, "INTAKE", row[5])
    # После отзыва оригинальной роли или передаче назначения другому
    # сотруднику реанимировать чужое доказательство нельзя.
    if (issuer != row[1] or row[2] != 0 or row[3] is not None
            or row[4] != "PENDING" or row[6] is not None or row[7] is not None):
        raise VehicleClaimRejected("unavailable")
    if conn.execute(
        "SELECT 1 FROM auto_vehicle_claim_reissue WHERE claim_id=%s",
        (row[0],),
    ).fetchone():
        raise VehicleClaimRejected("already reissued")
    # Повторная выдача считается отдельным ISSUE для общего ограничения.
    consume_attempt(conn, issuer, "ISSUE")
    replacement = secrets.token_urlsafe(32)
    conn.execute(
        """INSERT INTO auto_vehicle_claim_reissue(
             claim_id,issued_by,proof_hash,proof_expires_at)
           VALUES(%s,%s,%s,now()+interval '20 minutes')""",
        (row[0], issuer, hashlib.sha256(replacement.encode()).hexdigest()),
    )
    _audit(conn, link=link_id, claim=row[0], actor=issuer, action="REISSUED")
    return row[0], replacement


def redeem_auto_proof(
    conn: psycopg.Connection, client_token: str, claim_id: UUID, proof: str,
    idempotency_key: str | None = None,
) -> str:
    actor = session_subject(conn, client_token, for_mutation=True)
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
    if row is None or row[2] is not None or row[3] is not None:
        raise VehicleClaimRejected("unavailable")
    # Проверяем роль и конкретную customer-link даже при replay receipt.
    # Клиентская роль, сам пользователь и customer entitlement сохраняются
    # действительными до commit предъявления proof (без чтения чужих данных).
    eligible = conn.execute(
        """SELECT 1 FROM account_customer_link l
           JOIN user_role ur ON ur.user_id=l.user_id AND ur.role_code='CLIENT'
             AND ur.revoked_at IS NULL
             AND (ur.expires_at IS NULL OR ur.expires_at>now())
           JOIN app_user u ON u.id=l.user_id AND u.status='ACTIVE'
           WHERE l.user_id=%s AND l.customer_id=%s AND l.revoked_at IS NULL
           FOR SHARE OF l,ur,u""",
        (actor, row[1]),
    ).fetchone()
    if eligible is None:
        raise VehicleClaimRejected("customer not linked")
    # Второй SELECT выполняется ПОСЛЕ row lock на c,l: когда конкурентный
    # перевыпуск завершился, новый snapshot гарантированно видит его запись.
    # Это устраняет гонку старого proof с reissue без изменения исходного claim.
    version = conn.execute(
        """SELECT proof_hash,proof_expires_at FROM auto_vehicle_claim_reissue
           WHERE claim_id=%s""",
        (claim_id,),
    ).fetchone()
    effective_hash, effective_expiry = (version if version else (row[4], row[5]))
    payload = {"proof": proof}
    previous = retry_lookup(
        conn, actor, claim_id, "redeem", idempotency_key, payload,
    )
    if previous is not None:
        # Terminal state/expiry/revocation never replays a stale success.
        if row[8] != "PENDING" or effective_expiry <= datetime.now(timezone.utc):
            raise AutoClaimRetryConflict("claim no longer redeemable")
        if (previous == "PENDING_REVIEW" and row[7] == actor) or (
            previous == "REJECTED" and row[7] is None
        ):
            return previous
        raise AutoClaimRetryConflict("claim state changed")
    if (row[7] is not None or row[8] != "PENDING"
            or row[6] >= 5 or effective_expiry <= datetime.now(timezone.utc)):
        raise VehicleClaimRejected("unavailable")
    # Только новая попытка расходует общий лимит; повтор того же receipt — нет.
    # Ключ идемпотентности не даёт обходить учёт между разными claim.
    consume_attempt(conn, actor, "REDEEM")
    valid = hmac.compare_digest(
        hashlib.sha256(proof.encode()).hexdigest(), effective_hash,
    )
    conn.execute(
        """UPDATE auto_vehicle_claim
           SET proof_attempts=proof_attempts+1,
               claimant_id=CASE WHEN %s THEN %s ELSE NULL END,
               claimed_at=CASE WHEN %s THEN now() ELSE NULL END
           WHERE id=%s""",
        (valid, actor, valid, claim_id),
    )
    status = "PENDING_REVIEW" if valid else "REJECTED"
    _audit(conn, link=row[0], claim=claim_id, actor=actor,
           action="PROOF_ACCEPTED" if valid else "PROOF_REJECTED")
    retry_remember(conn, actor, claim_id, "redeem", idempotency_key, payload, status)
    return status


def review_auto_claim(
    conn: psycopg.Connection, manager_token: str, claim_id: UUID, approve: bool,
    idempotency_key: str | None = None,
) -> str:
    if type(approve) is not bool:
        raise VehicleClaimRejected("invalid decision")
    row = conn.execute(
        """SELECT c.link_id,c.issued_by,c.claimant_id,c.claimed_at,c.status,
                  l.vehicle_id,l.customer_id,l.revoked_at,l.verified_at,c.reviewed_by
           FROM auto_vehicle_claim c
           JOIN auto_vehicle_customer_link l ON l.id=c.link_id
           WHERE c.id=%s FOR UPDATE OF c,l""",
        (claim_id,),
    ).fetchone()
    if row is None:
        raise VehicleClaimRejected("unavailable")
    reviewer = _role_scoped(conn, manager_token, "MANAGER", row[5])
    if reviewer in (row[1], row[2]) or row[2] is None or row[3] is None:
        raise VehicleClaimRejected("independent reviewer required")
    # Проверяем живое полномочие claimant даже после финального решения.
    # Независимое review удерживает клиентские полномочия до commit;
    # отзыв до этой проверки даст отказ, после неё ожидает завершения.
    claimant_valid = conn.execute(
        """SELECT 1 FROM app_user u
           JOIN user_role ur ON ur.user_id=u.id AND ur.role_code='CLIENT'
              AND ur.revoked_at IS NULL
              AND (ur.expires_at IS NULL OR ur.expires_at>now())
           JOIN account_customer_link l ON l.user_id=u.id AND l.customer_id=%s
             AND l.revoked_at IS NULL
           WHERE u.id=%s AND u.status='ACTIVE'
           FOR SHARE OF u,ur,l""",
        (row[6], row[2]),
    ).fetchone()
    if claimant_valid is None or row[7] is not None:
        raise VehicleClaimRejected("claimant access revoked")
    outcome = "VERIFIED" if approve else "DECLINED"
    previous = retry_lookup(
        conn, reviewer, claim_id, "review", idempotency_key,
        {"approve": approve},
    )
    if previous is not None:
        if (previous == outcome and row[4] == outcome and row[9] == reviewer
                and (row[8] is not None) == approve):
            return previous
        raise AutoClaimRetryConflict("review no longer valid")
    if row[4] != "PENDING" or row[8] is not None:
        raise VehicleClaimRejected("unavailable")
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
    retry_remember(
        conn, reviewer, claim_id, "review", idempotency_key,
        {"approve": approve}, outcome,
    )
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
