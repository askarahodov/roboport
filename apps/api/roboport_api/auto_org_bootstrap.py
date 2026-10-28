"""Offline synthetic organization ADMIN bootstrap with independent signed approvals.

No HTTP route. Trust keys are injected out-of-band, never stored in the DB.
This is a dev approval proof, NOT a production identity verification process.
"""
from datetime import datetime, timezone
from hashlib import sha256
import hmac
from uuid import UUID, uuid4

import psycopg


class BootstrapDenied(ValueError):
    pass


def approval_message(org: UUID, target: UUID, expires: int, nonce: str) -> bytes:
    if (not isinstance(org, UUID) or not isinstance(target, UUID)
            or not isinstance(expires, int) or isinstance(expires, bool)
            or not isinstance(nonce, str) or not 16 <= len(nonce) <= 128
            or not nonce.isascii() or not nonce.isalnum()):
        raise BootstrapDenied("invalid approval statement")
    return f"auto-admin-v1:{org}:{target}:{expires}:{nonce}".encode("ascii")


def bootstrap_first_admin(
    conn: psycopg.Connection, org: UUID, target: UUID, expires: int,
    nonce: str, signatures: tuple[str, str], keys: tuple[bytes, bytes],
) -> UUID:
    """One-time, two-key dev bootstrap, atomic with independent audit receipt.

    Keys must come from independently administered channels. A caller with
    control over both keys can authorize arbitrary targets; deployment must
    NOT expose this method to web requests.
    """
    msg = approval_message(org, target, expires, nonce)
    now = int(datetime.now(timezone.utc).timestamp())
    if not now < expires <= now + 900:
        raise BootstrapDenied("approval expired or too far in future")
    if (len(keys) != 2 or len(signatures) != 2
            or not all(isinstance(k, bytes) and len(k) >= 32 for k in keys)
            or keys[0] == keys[1]
            or not all(isinstance(s, str) and len(s) == 64 for s in signatures)):
        raise BootstrapDenied("two distinct independent approvals required")
    for signature, key in zip(signatures, keys):
        try:
            raw = bytes.fromhex(signature)
        except ValueError as exc:
            raise BootstrapDenied("invalid approval signature") from exc
        if not hmac.compare_digest(raw, hmac.digest(key, msg, "sha256")):
            raise BootstrapDenied("approval rejected")
    with conn.transaction():
        organization = conn.execute(
            "SELECT id FROM auto_organization WHERE id=%s AND disabled_at IS NULL FOR UPDATE",
            (org,),
        ).fetchone()
        if organization is None:
            raise BootstrapDenied("organization unavailable")
        existing = conn.execute(
            """SELECT 1 FROM auto_organization_role_grant
               WHERE organization_id=%s AND role_code='ADMIN' LIMIT 1""",
            (org,),
        ).fetchone()
        if existing is not None:
            raise BootstrapDenied("organization already bootstrapped")
        user = conn.execute(
            "SELECT id FROM app_user WHERE id=%s AND status='ACTIVE' FOR SHARE",
            (target,),
        ).fetchone()
        if user is None:
            raise BootstrapDenied("target unavailable")
        proof_hash = sha256(msg).hexdigest()
        grant_id = uuid4()
        conn.execute(
            """INSERT INTO auto_organization_role_grant
               (id,organization_id,user_id,role_code)
               VALUES(%s,%s,%s,'ADMIN')""", (grant_id, org, target),
        )
        conn.execute(
            """INSERT INTO auto_org_bootstrap_receipt
               (id,organization_id,grant_id,target_id,statement_sha256,expires_at)
               VALUES(%s,%s,%s,%s,%s,to_timestamp(%s))""",
            (uuid4(), org, grant_id, target, proof_hash, expires),
        )
        return grant_id
