"""Идемпотентность закрытых create-операций без replay одноразовых секретов.

Сериализуется по (actor,operation,key), receipts неизменяемы. Все проверки
действующей сессии, объектного права и актуальности объекта выполняет caller
ДО чтения receipt и ПЕРЕД возвратом replay результата.
"""
import hashlib
import json
from uuid import UUID

import psycopg


class AutoIdempotencyConflict(Exception):
    """Ключ занят другим payload либо прежний результат уже недействителен."""


def validated_key(raw: str | None) -> str | None:
    if raw is None:
        return None
    if (not isinstance(raw, str) or not 16 <= len(raw) <= 128
            or not raw.isascii() or not raw.isprintable()
            or any(c.isspace() for c in raw)):
        raise ValueError("invalid idempotency key")
    return raw


def _digests(raw: str, payload: dict[str, str]) -> tuple[str, str]:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return (hashlib.sha256(raw.encode("ascii")).hexdigest(),
            hashlib.sha256(encoded.encode("ascii")).hexdigest())


def existing(
    conn: psycopg.Connection, actor: UUID, operation: str,
    key: str | None, payload: dict[str, str],
) -> UUID | None:
    if key is None:
        return None
    validated_key(key)
    key_hash, fingerprint = _digests(key, payload)
    # Advisory lock serializes identical key across *different* vehicle locks.
    # It is transaction-scoped and never substitutes for authorization.
    lock_id = f"{actor}:{operation}:{key_hash}"
    conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (lock_id,))
    row = conn.execute(
        """SELECT request_fingerprint,object_id,result_id
           FROM auto_create_receipt
           WHERE actor_id=%s AND operation=%s AND key_hash=%s""",
        (actor, operation, key_hash),
    ).fetchone()
    if row is None:
        return None
    if row[0] != fingerprint or str(row[1]) != payload["objectId"]:
        raise AutoIdempotencyConflict("idempotency key reused for different payload")
    return row[2]


def remember(
    conn: psycopg.Connection, actor: UUID, operation: str,
    key: str | None, payload: dict[str, str], result_id: UUID,
) -> None:
    if key is None:
        return
    key_hash, fingerprint = _digests(key, payload)
    conn.execute(
        """INSERT INTO auto_create_receipt(
             actor_id,operation,key_hash,request_fingerprint,object_id,result_id
           ) VALUES(%s,%s,%s,%s,%s,%s)""",
        (actor, operation, key_hash, fingerprint, UUID(payload["objectId"]), result_id),
    )
