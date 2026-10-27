"""Общий лимит попыток по действующей сессии, а не по ключу и не по заявке.

Счётчик в PostgreSQL одинаков для всех процессов. Успешный keyed replay
не расходует бюджет, новая попытка с новым ключом расходует. Не доверяем
X-Forwarded-For: источник IP требует отдельного доверенного proxy boundary.
"""
from uuid import UUID

import psycopg


class AutoProofRateLimited(Exception):
    """Возвращается только общий 429, без состояния чужих заявок."""


_LIMITS = {
    "ISSUE": (12, "1 hour"),
    "REDEEM": (8, "15 minutes"),
}


def consume_attempt(conn: psycopg.Connection, actor: UUID, operation: str) -> None:
    """Атомарно учитывать допустимую новую операцию внутри её транзакции."""
    limit, interval = _LIMITS[operation]
    count = conn.execute(
        """INSERT INTO auto_proof_throttle(actor_id,operation,attempts)
           VALUES(%s,%s,1)
           ON CONFLICT(actor_id,operation) DO UPDATE SET
             attempts=CASE
               WHEN auto_proof_throttle.window_started_at
                   <= now() - %s::interval THEN 1
               ELSE LEAST(auto_proof_throttle.attempts + 1, %s) END,
             window_started_at=CASE
               WHEN auto_proof_throttle.window_started_at
                   <= now() - %s::interval THEN now()
               ELSE auto_proof_throttle.window_started_at END
           RETURNING attempts""",
        (actor, operation, interval, limit + 1, interval),
    ).fetchone()[0]
    if count > limit:
        # Сохраняем блокировку окна: нельзя восстановить бюджет новыми ключами.
        raise AutoProofRateLimited("rate limit reached")
