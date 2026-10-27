"""Opt-in synthetic/dev limiter, scoped to direct numeric loopback peers.

Do NOT read HTTP forwarding headers. The packaged Uvicorn has proxy parsing
disabled; a separately approved ingress policy is required for production.
Only a keyed fingerprint of the ASGI socket peer is persisted in PostgreSQL.
"""
import hashlib
import hmac
import ipaddress
from collections.abc import Sequence

import psycopg

LIMIT = 30
WINDOW_SECONDS = 900


def peer_fingerprint(client: object, secret: str) -> str | None:
    """Return HMAC-SHA256 of numeric loopback peer, or fail closed.

    ASGI scope.client comes from the server's direct socket, not Host or XFF.
    A synthetic TestClient can inject it; this is *not* proof of a remote
    production peer or a trusted-proxy deployment.
    """
    if not isinstance(secret, str) or len(secret.encode("utf-8")) < 32:
        return None
    if (not isinstance(client, (tuple, list)) or len(client) != 2
            or not isinstance(client[0], str)
            or not isinstance(client[1], int)
            or isinstance(client[1], bool)
            or not 0 <= client[1] <= 65535):
        return None
    try:
        address = ipaddress.ip_address(client[0])
    except ValueError:
        return None
    if not address.is_loopback:
        return None
    canonical = address.compressed.encode("ascii")
    return hmac.new(
        secret.encode("utf-8"), b"auto-peer-v1:" + canonical, hashlib.sha256,
    ).hexdigest()


def consume_peer_request(dsn: str, fingerprint: str) -> bool:
    """Atomic cross-worker POST quota; rejected attempts still consume budget.

    Independent transaction intentionally commits before CSRF, session and
    business mutation. A 403/error/HTTP retry therefore cannot restore quota.
    """
    with psycopg.connect(dsn) as conn:
        attempts = conn.execute(
            """INSERT INTO auto_peer_throttle(peer_hmac,attempts)
               VALUES(%s,1)
               ON CONFLICT(peer_hmac) DO UPDATE SET
                 attempts=CASE
                   WHEN auto_peer_throttle.window_started_at <= now() - interval '15 minutes'
                     THEN 1
                   ELSE LEAST(auto_peer_throttle.attempts + 1, 31) END,
                 window_started_at=CASE
                   WHEN auto_peer_throttle.window_started_at <= now() - interval '15 minutes'
                     THEN now()
                   ELSE auto_peer_throttle.window_started_at END
               RETURNING attempts""",
            (fingerprint,),
        ).fetchone()[0]
    return attempts <= LIMIT
