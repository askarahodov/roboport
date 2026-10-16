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


## Фактически выполненный CI — 2026-10-16

SHA: `56ba8acaad577d7c2febd85fffd80773314fe685`

- [Run 38892930412](https://github.com/askarahodov/roboport/actions/runs/38892930412) — `success`; задания [postgres](https://github.com/askarahodov/roboport/actions/runs/38892930412/job/116946532340), [api](https://github.com/askarahodov/roboport/actions/runs/38892930412/job/116946532639), [web](https://github.com/askarahodov/roboport/actions/runs/38892930412/job/116946532768), [browser-e2e](https://github.com/askarahodov/roboport/actions/runs/38892930412/job/116946532699) — все `success` (exit 0).
- [Run 38892911739](https://github.com/askarahodov/roboport/actions/runs/38892911739) — `success`, тот же SHA.
- **AC-017: PASS (автоматический PG backup/restore gate)** — реальный `postgres:17`, миграции upgrade→downgrade→upgrade, `pg_dump -Fc`, `pg_restore --exit-on-error`, контроль marker/revision, чтение восстановленной записи и отрицательный тест FK. Доказательство: PostgreSQL job выше. *Не означает подтверждения производственного RTO/RPO.*
- **AC-019: PASS (CI jobs)** — api + postgres + web + browser-e2e завершены статусом `success` для указанного SHA. Доказательство: run и четыре job URL выше.
- **AC-001…016, AC-018, AC-020: BLOCKED (per-AC independent QA mapping)** — наборы могут быть исполнены зелёным CI, но granular exit и фактические сценарии необходимо сверить с логами до установки индивидуальных PASS. Отдельный MinIO workflow, visual snapshot review, two-session contention и legal/security approval должны быть подтверждены отдельно.
- **QA sign-off: BLOCKED**, рецензент не назначен; public-release gate **BLOCKED**. Это не блокирует дальнейшую разработку.

## Обнаруженное расхождение между зелёным CI и логами

В [MinIO run 38897787632](https://github.com/askarahodov/roboport/actions/runs/38897787632) шаг восстановления вывел `mc mirror ... Overwrite not allowed` для существующих объектов, несмотря на итоговый `success`. Поэтому **AC-012 (полное восстановление хранилища) нельзя засчитывать без исправления**. Коммит `8b0abe858eaa92d7b28a15debfe80b8e1d33b85f` добавляет `mc mirror --overwrite`; требуется повторная проверка MinIO workflow с отсутствием ошибок mirroring.

Фактические итоги основного [CI 38897787671](https://github.com/askarahodov/roboport/actions/runs/38897787671), commit `f0d5e45187ac5b59eb5ea9cbf1d65fcdb35fbb11`:

- Integration PostgreSQL: **80 passed, 4 skipped** (exit 0), [postgres job](https://github.com/askarahodov/roboport/actions/runs/38897787671/job/116962337146). Причины четырёх SKIP требуют отдельной проверки.
- Bootstrap Python: **96 passed** (exit 0); API contract: **51 passed** (exit 0), [api job](https://github.com/askarahodov/roboport/actions/runs/38897787671/job/116962337336).
- Chromium browser: **16 passed** (exit 0), [browser job](https://github.com/askarahodov/roboport/actions/runs/38897787671/job/116962336883).
- Restore FK probe: `RESTORE INTEGRITY PASS` подтверждён в логе postgres job.
- MinIO integration: **3 passed**, но ошибка `mc mirror` в шаге восстановления требует устранения до утверждения AC-012.

Автоматический уровень AC-017/019 — **PASS на SHA f0d5e451**; прочие пункты остаются **BLOCKED на уровне полного сценарного подтверждения и независимой приёмки**, даже если соответствующий тест входил в зелёный набор.

## Закрытие четырёх SKIP — независимые среды

Все четыре `SKIP` в PostgreSQL job относятся к MinIO-only тестам, которые целенаправленно исключены без переменной `ROBOport_MINIO_ENDPOINT=http://127.0.0.1:9000`:

1. `tests/integration/test_minio_storage.py` — 1 тест.
2. `tests/integration/test_pg_minio_attachment_http.py` — 3 теста.

Это не пропущенная функциональность: выделенный [MinIO run #38899744367](https://github.com/askarahodov/roboport/actions/runs/38899744367), commit `8b0abe858eaa92d7b28a15debfe80b8e1d33b85f`, исполнил **1 passed + 3 passed** на реальном MinIO + PostgreSQL. Исправленное восстановление объектов `mc mirror --overwrite` завершено без сообщений `Overwrite not allowed`. Основной [CI run #38899744465](https://github.com/askarahodov/roboport/actions/runs/38899744465) исполнил другие integration tests. Поэтому суммарный test coverage нельзя оценивать только по PostgreSQL job. Четыре SKIP разрешены доказательством альтернативного workflow.

Для завершающего коммита все соответствующие workflows обязаны завершиться заново. Проверка UI визуальной доступности человеком и независимое QA-review по-прежнему не засвидетельствованы.


## Проверенный итог полного автоматического набора на SHA 1037df95

**Исполняемая ветка:** `qa/issue-26-readiness-evidence`, SHA `1037df95efd897cc8e65067eb9044f851906dada`. Все перечисленные задания — **PASS / exit 0**:

| Исполнение | Доказательство | Факт |
|---|---|---|
| PostgreSQL 17, concurrency, HTTP/PG | [postgres job](https://github.com/askarahodov/roboport/actions/runs/38901211966/job/116973647627) | 80 passed, 4 skipped; миграции, dump/restore, FK probe — PASS |
| Auth + API contract | [api job](https://github.com/askarahodov/roboport/actions/runs/38901211966/job/116973647492) | bootstrap 96 passed; contract 51 passed |
| Browser Chrome | [browser job](https://github.com/askarahodov/roboport/actions/runs/38901211966/job/116973647204) | Playwright 16 passed |
| Web type/build | [web job](https://github.com/askarahodov/roboport/actions/runs/38901211966/job/116973647434) | PASS |
| Настоящий MinIO + PostgreSQL | [minio job](https://github.com/askarahodov/roboport/actions/runs/38901212008/job/116973647969) | storage 1 passed + attachment 3 passed; восстановление без mirroring errors, PASS |

Проверенные причины пропусков (лог `pytest -ra`): `test_minio_storage.py:10` + `test_pg_minio_attachment_http.py:14,64,114` — отсутствует MinIO в стандартном PostgreSQL job. Все четыре **PASS в выделенном MinIO job**.

### Итоговое отображение AC → реально исполняемый набор

| AC | Фактически запущенный набор / статус автоматизации | Ограничение |
|---|---|---|
| 001–003 | bootstrap/contract + PG integration **PASS** | ручная оценка catalog evidence ещё не подписана |
| 004–006 | PG integration **PASS** | ручная оценка corner cases ещё не подписана |
| 007–011 | PG integration + auth bootstrap **PASS** | независимый security sign-off отсутствует |
| 012 | MinIO + PG HTTP, 4 dedicated tests **PASS** | production storage compliance не проверялась |
| 013 | Browser 16 tests **PASS** | ручное сравнение Light/Dark/390px снимков отсутствует |
| 014 | Browser 16 tests **PASS** | WCAG/keyboard independent review отсутствует |
| 015 | API 51 contract tests **PASS** | публичный compliance gate не проверен |
| 016 | PG integration **PASS** | независимый replay audit отсутствует |
| 017 | PG restore + migration + FK **PASS** | production RTO/RPO не проверялись |
| 018 | PG integration **PASS** | crash/restart эксплуатационный сценарий требует независимой сверки |
| 019 | 4 core CI jobs **PASS** | только на указанном SHA |
| 020 | FSM tests в PG/contract **PASS** | operational robot safety approval отсутствует |

**Важная граница:** PASS выше означает подтверждённый exit соответствующего автоматического набора, не завершённый ручной аудит индивидуального AC. Без отдельной подписи reviewer запрещено менять статус задачи на Closed или объявлять public-release ready. Задача #24 также OPEN — её код в этом PR не интегрирован.


## Exit criteria

1. Для **каждого** AC записать фактический PASS/FAIL/SKIP/BLOCKED, run URL, SHA, команду, exit code, найденный дефект/ограничение.
2. Отдельно проверить сохранённые Chromium screenshots в разрешениях 390px и desktop и keyboard focus. Автоматическая проверка DOM — не эквивалент accessibility-аудиту.
3. Отдельный QA (не автор патча) должен оставить review со ссылками на результаты. До него issue остаётся OPEN.
4. `dev-gate PASS` не означает `public release-ready`: legal, privacy, safety и security approvals остаются независимыми блокирующими решениями.
5. Привязка к #24 и #25 обязательна; код ветки `main` не включает открытые PR по умолчанию.

## Образец реестра завершения

`AC-017 | PASS | commit=<sha> | run=<url> | postgres job=<url> | command=... | exit=0 | reviewer=<github-login>`

Доказательства исходного среза и CI должны быть доступны другому инженеру; `PASS` без ссылки и команды недействителен.
