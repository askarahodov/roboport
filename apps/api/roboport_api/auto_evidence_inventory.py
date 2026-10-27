"""Безопасный агрегированный осмотр auto-evidence до утверждения retention.

Никакие удаление/архивирование receipts, claim и append-only аудита здесь
не реализованы: сроки хранения и последствия для retry требуют LEGAL/#119.
Выбор SQL-идентификаторов идёт только из закрытого allowlist.
"""
from psycopg import sql


# Только таблицы, у которых реально существует created_at в Alembic 0022–0029.
EVIDENCE_TABLES = (
    "auto_create_receipt",
    "auto_claim_retry_receipt",
    "auto_vehicle_claim",
    "auto_vehicle_claim_reissue",
    "auto_vehicle_claim_audit",
    "auto_vehicle_link_intake_audit",
    "auto_vehicle_staff_assignment_audit",
)

# Не допускаем операции по чужим таблицам или список, заданный через CLI.
_QUERY = sql.SQL("""
    SELECT
      count(*)::bigint,
      count(*) FILTER (
        WHERE created_at >= now() - interval '1 day'
      )::bigint,
      count(*) FILTER (
        WHERE created_at >= now() - interval '7 days'
          AND created_at < now() - interval '1 day'
      )::bigint,
      count(*) FILTER (
        WHERE created_at >= now() - interval '30 days'
          AND created_at < now() - interval '7 days'
      )::bigint,
      count(*) FILTER (
        WHERE created_at < now() - interval '30 days'
      )::bigint
    FROM {}
""")


def evidence_inventory(conn) -> dict[str, dict[str, int]]:
    """Агрегаты по фиксированным таблицам, без UUID, proof, ключей и строк."""
    report = {}
    for table in EVIDENCE_TABLES:
        row = conn.execute(_QUERY.format(sql.Identifier(table))).fetchone()
        report[table] = dict(zip(
            ("total", "last24h", "days1to7", "days7to30", "older30d"),
            row,
        ))
    return report
