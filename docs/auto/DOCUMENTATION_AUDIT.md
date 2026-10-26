# Аудит документации на момент перехода к Airmatik Pro Service

**Дата:** 2026-10-26  
**Задача:** [#122](https://github.com/askarahodov/roboport/issues/122)  
**Основа проверки:** дерево исходников, Alembic 0001–0021, `apps/web/package.json`, `apps/api/requirements-dev.txt`, `.github/workflows/ci.yml`, существующие UI/HTTP/PG-тесты и пройденные CI для PR #120 и #126.

Документы разделяются на три состояния: **current** — подтверждаемое исходниками/проверками; **legacy** — относится к работающему робототехническому домену; **planned** — дизайн будущего автосервиса, не готовая функция.

## Реальная готовность на момент аудита

| Подсистема | Состояние | Где проверять |
|---|---|---|
| Python API, FastAPI | current, но обслуживает преимущественно robot domain | `apps/api/roboport_api/main.py`, `tests/bootstrap/`, `tests/contract/` |
| PostgreSQL 17, Alembic | current для dev и синтетических тестов; head 0021_auto_vehicle | `apps/api/alembic/versions/`, `tests/integration/`, CI postgres |
| React, TypeScript, Vite 7.1.12 | current, тестовый web UI роботов/клиентов/сотрудников | `apps/web/package.json`, `apps/web/src/`, `apps/web/e2e/`, CI web/browser-e2e |
| Local sessions, Keycloak/OIDC, RBAC/ACL | current для синтетических тестов; не production approval | `apps/api/roboport_api/auth_http.py`, `authorization.py`, `oidc.py`, интеграционные тесты |
| MinIO, приватные вложения | dev integration current, но не внешний S3 production | `infra/dev/compose.local.yml`, `storage.py`, `tests/integration/test_minio_storage.py` |
| Сметы, остатки и outbox | current в robot domain, не в auto заказ-наряде | `estimate_decision.py`, `inventory.py`, `notification_outbox.py` |
| Автомобили и VIN | схема `auto_vehicle`, `auto_vehicle_customer_link` и **read-only dev-HTTP** `/api/v1/dev/auto/vehicles` (только synthetic); публичный REST/UX ещё planned | `apps/api/alembic/versions/0021_auto_vehicle.py`, `tests/integration/test_pg_auto_vehicle.py`; #112 |
| Запись, рабочие посты, автомобильные наряды | planned | #113, #114 |
| OEM-каталоги 2026, поставщики, коммерческие лицензии | planned, исследование без ключей | #111, #115, `docs/auto/PARTS_CATALOG.md` |
| Клиентский автомобильный кабинет, мобильное приложение | planned | #117, #118 |
| Публичный Airmatik-бренд, реальные клиенты/оплаты | blocked до legal/privacy/brand/release gate | #110, #116, #119 |

**Важно:** успешный CI подтверждает текущий синтетический робототехнический код и проверку аддитивной таблицы авто, **но не** готовый пользовательский автомобильный сервис.

## Исправленные расхождения

1. `README.md` описывал только три готовых каталожных эндпоинта, несмотря на доступные синтетические модули авторизации, заявок, смет, склада и файлов.
2. `docs/TECH_STACK.md` указывал Vite 8, отсутствие PostgreSQL, lockfile, CI, OIDC, MinIO и outbox. В `apps/web/package.json` фактически Vite 7.1.12; тестовый CI запускался.
3. `docs/operations/DEV_ENVIRONMENT.md` утверждал, что `npm ci` заблокирован из-за отсутствия lockfile и Keycloak отложен. Источник — `apps/web/package-lock.json`, `infra/dev/LOCAL_STACK.md`, CI.
4. `docs/api/API_IMPLEMENTATION.md` представлял большую часть API как backlog, хотя backend содержит реальные synthetic handlers. Там же нельзя приравнивать наличие операции в OpenAPI к реализации.
5. `TECHNICAL_SPEC.md` конфликтовал с новой продуктовой целью, продолжая утверждать, что является каноническим ТЗ для **любого** нового развития; теперь документ явно ограничен legacy-робототехникой.
6. `docs/auto/ARCHITECTURE.md` ошибочно называл Alembic head 0020, хотя PR #120 добавил 0021.

## Канонические источники по области

| Область | Что читать в первую очередь |
|---|---|
| Бизнес-проектирование автосервиса | [PRODUCT.md](PRODUCT.md), [FUNCTIONAL_MATRIX.md](FUNCTIONAL_MATRIX.md) |
| Архитектура новой предметной области | [ARCHITECTURE.md](ARCHITECTURE.md), [ADR-001.md](ADR-001.md) |
| Текущий робототехнический домен | [TECHNICAL_SPEC.md](../../TECHNICAL_SPEC.md) — **legacy**; существующие `docs/domain/`, `docs/data/` |
| Базовые технические соглашения | [AGENTS.md](../../AGENTS.md), [TECH_STACK.md](../TECH_STACK.md), [DOCUMENTATION_POLICY.md](../DOCUMENTATION_POLICY.md) |
| Сборка и dev окружение | [DEV_ENVIRONMENT.md](../operations/DEV_ENVIRONMENT.md), [LOCAL_STACK.md](../../infra/dev/LOCAL_STACK.md), `.github/workflows/` |
| Контракт API | `contracts/openapi.yaml` и `tests/contract/`, а не только число операций |
| База знаний пользователей | [Индекс по ролям](../guides/INDEX.md) — каркас, инструкция должна иметь `current` перед использованием |

## Незакрытые риски

- Проверить полное соответствие OpenAPI каждому HTTP-handler: отдельные contract tests должны обнаруживать несовпадения.
- Перед включением живого авто-UI разработать server-side ACL для автомобилей, подтверждение связей и защиту старой истории.
- Проверить на практике и записать точные Windows CMD инструкции: shell-скрипты `bootstrap-local.sh` не работают напрямую в CMD.
- В будущем сделать публикацию документации и автоматическую валидацию ссылок/якорей (задача #125).
- Перед реальным релизом выполнить повторный аудит технической документации, пользовательских сценариев и локальных нормативных требований.

## Как поддерживать

Каждый функциональный PR документирует `Documentation impact` в том же PR; интерфейс/контракт меняются вместе с инструкцией по соответствующей роли. Никаких реальных клиентских данных/телефонов/VIN/токенов в документации.

## Промежуточная реализация после исходного аудита

После первичной фиксации документа обновлена схема до миграции `0022_auto_vehicle_claim` (scope assignments, issue/redeem/review/revoke), реализован только синтетический staff/client HTTP workflow. Он **не** меняет первоначальный вывод аудита о неготовности публичной автомобильной CRM. Тестовые процедуры и права: [VEHICLE_OWNERSHIP.md](VEHICLE_OWNERSHIP.md).
