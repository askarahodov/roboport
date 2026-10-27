"""Атомарный retry для auto claim без хранения открытого proof в receipts."""
import hashlib
import hmac
import json
from uuid import UUID

import psycopg

from .auto_create_idempotency import validated_key


class AutoClaimRetryConflict(Exception):
    """Ключ занят иными параметрами либо прежний эффект больше недействителен."""


def lookup(
    conn: psycopg.Connection, actor: UUID, claim: UUID,
    operation: str, key: str | None, payload: dict[str, object],
) -> str | None:
    if key is None:
        return None
    validated_key(key)
    key_hash, fingerprint = _hashes(key, claim, payload)
    # Lock нужен до чтения receipt: разные claim с одним actor/key тоже сериализованы.
    conn.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
        (f"auto-claim:{actor}:{operation}:{key_hash}",),
    )
    row = conn.execute(
        """SELECT claim_id,request_fingerprint,outcome FROM auto_claim_retry_receipt
           WHERE actor_id=%s AND operation=%s AND key_hash=%s""",
        (actor, operation, key_hash),
    ).fetchone()
    if row is None:
        return None
    if row[0] != claim or not hmac.compare_digest(row[1], fingerprint):
        raise AutoClaimRetryConflict("key reused for different claim or payload")
    return row[2]


def remember(
    conn: psycopg.Connection, actor: UUID, claim: UUID, operation: str,
    key: str | None, payload: dict[str, object], outcome: str,
) -> None:
    if key is None:
        return
    key_hash, fingerprint = _hashes(key, claim, payload)
    conn.execute(
        """INSERT INTO auto_claim_retry_receipt(
             actor_id,claim_id,operation,key_hash,request_fingerprint,outcome)
           VALUES(%s,%s,%s,%s,%s,%s)""",
        (actor, claim, operation, key_hash, fingerprint, outcome),
    )


def _hashes(key: str, claim: UUID, payload: dict[str, object]) -> tuple[str, str]:
    key_hash = hashlib.sha256(key.encode("ascii")).hexdigest()
    # Proof существует только в памяти; в БД фиксируется его fingerprint.
    raw = json.dumps(
        {"claimId": str(claim), **payload}, sort_keys=True,
        separators=(",", ":"), ensure_ascii=True,
    ).encode("ascii")
    return key_hash, hashlib.sha256(raw).hexdigest()
