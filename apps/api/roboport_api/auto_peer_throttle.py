"""Добровольно включаемый dev-лимит для числового прямого loopback peer.

HTTP-заголовки переадресации не являются источником сетевого адреса.
В поставляемом Uvicorn их обработка отключена; для production нужна
отдельно утверждённая ingress-политика. PostgreSQL хранит только HMAC peer.
"""
import hashlib
import hmac
import ipaddress

import psycopg

LIMIT = 30
WINDOW_SECONDS = 900


def peer_fingerprint(client: object, secret: str) -> str | None:
    """Вернуть HMAC-SHA256 числового loopback peer либо запретить запрос.

    ASGI scope.client задаётся сервером прямого соединения, не Host или XFF.
    TestClient может эмулировать peer; это не доказательство подлинности
    удалённого production-клиента и не замена доверенной proxy-схемы.
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
    # В Python 3.13 ::ffff:127.0.0.1 считается loopback. В dev-пилоте
    # допускаем только однозначный нативный IPv4 или IPv6 peer.
    if (not address.is_loopback
            or (isinstance(address, ipaddress.IPv6Address)
                and address.ipv4_mapped is not None)):
        return None
    canonical = address.compressed.encode("ascii")
    return hmac.new(
        secret.encode("utf-8"), b"auto-peer-v1:" + canonical, hashlib.sha256,
    ).hexdigest()


def consume_peer_request(dsn: str, fingerprint: str) -> bool:
    """Атомарный межпроцессный POST-лимит: отказы тоже расходуют квоту.

    Отдельная транзакция коммитится до проверки CSRF/сессии и бизнес-операции,
    поэтому ответ 403, ошибка и повтор HTTP-запроса не возвращают бюджет.
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
