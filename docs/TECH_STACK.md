# Технологический стек — текущее dev-состояние ROBOport и переход к Airmatik

**Состояние: current для инфраструктурных компонентов, legacy для робототехнической бизнес-логики, planned для автомобильных функций.** Дата сверки: 26.10.2026. См. [аудит](auto/DOCUMENTATION_AUDIT.md).

| Уровень | Технологии / источник | Подтверждённое состояние |
|---|---|---|
| Backend | Python 3.13, FastAPI 0.143.1, Pydantic 2.14.1; `apps/api/requirements-dev.txt` | HTTP API и тесты текущего robot domain |
| PostgreSQL | PostgreSQL 17, psycopg 3, Alembic 1.16.5; `apps/api/alembic/versions/` | Исполняемая история 0001–0021; интеграционные тесты на одноразовой БД |
| Web | Node.js 24, React 19.2, TypeScript 5.9, **Vite 7.1.12**; `apps/web/package.json` | Client/staff/public web для существующего домена; npm lockfile и build в CI |
| Маршруты Web | react-router-dom 7.9.4 | Есть, TanStack Query **не** указан в зависимостях и не считается внедрённым |
| Auth | Local accounts, Argon2id, opaque cookie sessions, OIDC/Keycloak для dev | Backend handlers + тесты, в том числе signed JWKS, PKCE; production ещё не одобрен |
| Доступ | RBAC, ACL/ownership checks | Тестовые проверки на уровне объектов; перенос в auto домен ещё планируется |
| API контракт | OpenAPI 3.1, генератор openapi-typescript 7.10.1 | Контракт и tests; факт наличия схемы не доказывает готовность конкретного endpoint |
| Тесты | pytest, httpx, Playwright 1.56.1, tsc, GitHub Actions | CI postgres/api/web/browser-e2e проходил для PR #120 и #126 |
| Файлы | S3-compatible MinIO в приватном dev контуре | Адаптер и интеграционные тесты для synthetic files; production storage не согласован |
| Задачи | PostgreSQL transactional outbox + Python worker | Имеются в текущем robot domain, не подключены к будущему auto order |
| Инфраструктура | `infra/dev/compose.local.yml` и `infra/dev/LOCAL_STACK.md` | Compose инфраструктура PG/MinIO/Keycloak; Web/API стартуют на host с dev security guards |
| Дизайн | существующая палитра Industrial C | Legacy; новый бренд Airmatik требует подтверждения прав и стиля по #110 |

## Сохранённые архитектурные решения

Модульный монолит, FastAPI, PostgreSQL и React; сложные операции — внутри ACID транзакций. Микросервисы, Kafka, Redis, Kubernetes/Helm не добавлять без потребности и ADR. Автомобильный домен развивается отдельно по [ADR-AUTO-001](auto/ADR-001.md), не через глобальное переименование таблиц.

## Управление версиями и зависимости

Источники точных версий — `apps/web/package-lock.json`, `apps/web/package.json`, `apps/api/requirements-dev.txt` и `apps/api/constraints-py313.txt`. Python прямые зависимости зафиксированы, но хеши всех транзитивных пакетов пока не являются полным воспроизводимым lock. Изменения стека только через ADR с миграцией и тестами.

## Что ещё не является готовым функционалом

Auto Vehicle только на уровне аддитивной базы данных (#112); полноценная запись, посты, автомобильные наряды, OEM-каталоги и мобильное приложение — planned (#111–#118). Реальные ПДн/платежи/внешнее использование отключены до release gates.

См. [архитектуру авто](auto/ARCHITECTURE.md), [окружение](operations/DEV_ENVIRONMENT.md), [правила актуальности](DOCUMENTATION_POLICY.md).
