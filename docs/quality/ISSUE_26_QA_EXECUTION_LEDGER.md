# QA execution ledger — Issue #26

> Срез ветки `main` на 2026-10-16: `3d78618e86b6cd75299167a6f48b168420acf5cd`.
> Этот документ **не** является подписанной независимой QA-приёмкой. CI статусы заполняются **только после** завершения workflow для конкретного SHA. Нельзя приравнивать успешную сборку к 20 закрытым AC.

Окружение: GitHub Actions `ubuntu-latest`, Python 3.13, Node 24, PostgreSQL 17 docker service, Chromium Playwright, только синтетические данные. MinIO имеет отдельный workflow `.github/workflows/minio-dev.yml`. 
Команды и exit фиксируются в логах CI: `python -m pytest -q tests/integration`; `PYTHONPATH=apps/api python -m pytest -q tests/bootstrap`; `PYTHONPATH=apps/api python -m pytest -q tests/contract`; `cd apps/web && npm run typecheck && npm run build && npm run test:e2e`; `python -m alembic -c apps/api/alembic.ini upgrade head`, `downgrade base`, `upgrade head`; `pg_dump -Fc` / `pg_restore --exit-on-error`.

Статус **BLOCKED** = не получена независимая проверка для конкретного SHA; статус **SKIP** = проверка сознательно не исполняется в указанном workflow. Для каждой AC нужна отдельная привязка к конкретному результату, без вывода PASS из наличия файла.

| AC | Исполняемое доказательство / команда | Статус до независимой верификации |
|---|---|---|
| AC-001 | `tests/bootstrap/test_public_catalog.py`, `tests/contract/test_catalog_http.py` | BLOCKED |
| AC-002 | `tests/integration/test_pg_catalog_capability.py` | BLOCKED |
| AC-003 | `tests/integration/test_pg_catalog_capability.py` | BLOCKED |
| AC-004 | `tests/integration/test_pg_inventory.py`, `test_pg_cross_operation_race.py` | BLOCKED |
| AC-005 | `tests/integration/test_pg_ticket_fsm.py` | BLOCKED |
| AC-006 | `tests/integration/test_pg_estimate_staff_lifecycle.py`, `test_pg_estimate_approval.py` | BLOCKED |
| AC-007 | `tests/integration/test_http_ticket_access.py`, `test_pg_attachment_access_http.py` | BLOCKED |
| AC-008 | `tests/integration/test_pg_device_transfer_history.py` | BLOCKED |
| AC-009 | `tests/integration/test_pg_ticket_fsm.py`, `test_pg_idempotency_concurrency.py` | BLOCKED |
| AC-010 | `tests/integration/test_http_login_flow.py`, `test_pg_session_service.py` | BLOCKED |
| AC-011 | `tests/integration/test_pg_ownership_guard.py` | BLOCKED |
| AC-012 | `tests/integration/test_pg_minio_attachment_http.py`, `test_minio_storage.py` + separate MinIO workflow | BLOCKED |
| AC-013 | `apps/web/e2e/*.spec.ts` + retained screenshot artifact | BLOCKED |
| AC-014 | `apps/web/e2e/*.spec.ts` + manual accessibility review | BLOCKED |
| AC-015 | `tests/contract/test_response_schemas.py`, `test_openapi_validator.py` | BLOCKED |
| AC-016 | `tests/integration/test_pg_idempotency_concurrency.py` | BLOCKED |
| AC-017 | CI PostgreSQL migration steps + backup/restore roundtrip, restored FK integrity | BLOCKED |
| AC-018 | `tests/integration/test_pg_notification_outbox.py` | BLOCKED |
| AC-019 | CI: postgres, api, web, browser-e2e job status for exact SHA | BLOCKED |
| AC-020 | `tests/contract/test_ticket_transition_graph.py` + independent real safety review | BLOCKED |

## Exit criteria

1. Для **каждого** AC записать фактический PASS/FAIL/SKIP/BLOCKED, run URL, SHA, команду, exit code, найденный дефект/ограничение.
2. Отдельно проверить сохранённые Chromium screenshots в разрешениях 390px и desktop и keyboard focus. Автоматическая проверка DOM — не эквивалент accessibility-аудиту.
3. Отдельный QA (не автор патча) должен оставить review со ссылками на результаты. До него issue остаётся OPEN.
4. `dev-gate PASS` не означает `public release-ready`: legal, privacy, safety и security approvals остаются независимыми блокирующими решениями.
5. Привязка к #24 и #25 обязательна; код ветки `main` не включает открытые PR по умолчанию.

## Образец реестра завершения

`AC-017 | PASS | commit=<sha> | run=<url> | postgres job=<url> | command=... | exit=0 | reviewer=<github-login>`

Доказательства исходного среза и CI должны быть доступны другому инженеру; `PASS` без ссылки и команды недействителен.
