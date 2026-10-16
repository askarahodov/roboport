# Проверка PostgreSQL и Alembic — Issue #11

Срез работает только с `postgres:17` в disposable-контуре GitHub Actions, не использует боевые данные.

| AC | Реальная исполняемая проверка |
|---|---|
| AC1 | `python -m alembic -c apps/api/alembic.ini upgrade head` |
| AC2 | `python -m alembic -c apps/api/alembic.ini downgrade base`, затем повторный `upgrade head` |
| AC3 | `python -m pytest -q tests/integration` на PostgreSQL 17 |
| AC4 | PG-негативные FK, владелец заявки, immutable SENT-смета, append-only audit, неотрицательный склад; полный набор в `tests/integration` |
| AC5 | `pg_dump -Fc` → `createdb roboport_restored` → `pg_restore`; сверка маркера и `alembic_version`; проверка возможности вставить audit record после восстановления |
| AC6 | Требуется независимое review именно по текущему коммиту и PostgreSQL-логам; при отсутствии подтверждения оставлять issue OPEN |

Верификация действующего `main` (до изменения каталога):
https://github.com/askarahodov/roboport/actions/runs/38871235132 — SUCCESS, включая PostgreSQL job.

## Перенумерация схем для следующих задач

- Последняя каноническая миграция `main`: `0014_estimate_decision`.
- Каталог (#18) предложен через `0015_catalog`; склад (#21) через `0015_inventory` на разных feature-ветках.
- **Нельзя** merge обе версии 0015. После принятия каталога складская миграция должна ссылаться на каталог как предка и использовать следующий уникальный revision id.
- При совмещении каталог должен быть источником правды по part compatibility/evidence; не создавать два независимых определения одного допуска.

Исторические SQL-черновики `db/legacy_drafts` не исполнять.
