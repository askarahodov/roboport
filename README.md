# ROBOport — платформа сервисного обслуживания робототехники

> **Новая продуктовая цель (26.10.2026):** переход к системе автосервиса Airmatik Pro Service. [Продукт](docs/auto/PRODUCT.md), [архитектура](docs/auto/ARCHITECTURE.md), [матрица](docs/auto/FUNCTIONAL_MATRIX.md), [ADR](docs/auto/ADR-001.md), [каталоги 2026](docs/auto/PARTS_CATALOG.md), [Epic #107](https://github.com/askarahodov/roboport/issues/107). Ниже описана существующая робототехническая реализация, которая не является готовым автосервисом. Миграция поэтапная; dev — только на синтетических данных.

> **Состояние:** подготовка разработки; реальные клиенты, персональные данные, платежи и публичный запуск пока не допускаются. Этот репозиторий публичный.

ROBOport — единая система независимого мультибрендового сервиса домашних роботов: публичный сайт и каталог, **обязательный личный кабинет клиента**, рабочее место сотрудников (CRM), заявки, диагностика, сметы, контроль совместимости деталей, склад, контроль качества, документы и аудит.

## С чего начинать человеку и ИИ-агенту

1. [Полное техническое задание](TECHNICAL_SPEC.md) — бизнес-правила и границы MVP.
2. [Карта документации](docs/INDEX.md) — что читать в зависимости от задачи.
3. [Структура репозитория](docs/REPOSITORY_STRUCTURE.md) — где размещать код и тесты.
4. [Технологический стек](docs/TECH_STACK.md) — выбранные и отложенные решения.
5. [Правила работы ИИ-агентов](AGENTS.md) и [единый формат GitHub Issues](docs/implementation/ISSUE_STANDARD.md).
6. [Реестр задач и зависимостей](docs/implementation/WORK_BREAKDOWN.md) — **номер GitHub Issue является единственным номером задачи**.
7. [Этапы/очередность](docs/implementation/DEVELOPMENT_ROADMAP.md).

## Текущее состояние исходников

- `apps/api`: FastAPI, проверяемые health/meta и синтетический read-only каталог брендов/моделей. Три операции каталога реализованы, прочие функции — нет.
- `apps/web`: каркас React/Vite с выбранным угловым знаком **C Industrial Lab**, бирюзовой палитрой A, режимами Light/Dark/Auto. Полный web build в CI пока не подтверждён.
- `contracts/openapi.yaml`: **проектный** OpenAPI 3.1, 42 операции, 56 схем. Нельзя путать с реализованным API.
- `db/legacy_drafts`: шесть исторических SQL-черновиков. **Не применять к реальной базе** — сначала сверить и перенести в Alembic с тестами на одноразовом PostgreSQL.
- `design`: редактируемые SVG знака C, токены и демонстрационный прототип; юридическая проверка и полный брендбук ещё не закончены.

## Технический стек

Python 3.13 · FastAPI · Pydantic 2 · PostgreSQL 17 · SQLAlchemy 2 · Alembic · React 19 · TypeScript 5 · Vite 8 · Node.js 24 · pytest · Playwright · GitHub Actions. Для первого этапа — **локальные учётные записи PostgreSQL**, Argon2id, серверные cookie-сессии; права **RBAC + ACL** (#13/#27). Внешний OIDC/Keycloak отложен. Для файлов в разработке выбран приватный **MinIO** (#28/#25), **не нужен для каталога #12**. Подробно: [стек и ADR](docs/TECH_STACK.md).


## Воспроизводимое локальное развёртывание

Для PostgreSQL 17, Keycloak и MinIO используйте [локальный dev-стенд](infra/dev/LOCAL_STACK.md): `infra/dev/compose.local.yml` и `infra/dev/bootstrap-local.sh`. Стенд проверяется реальным CI `ROBOport local stack smoke`; API и frontend запускаются на хосте для сохранения loopback-only защитных проверок. Полный HTTPS reverse proxy для объединённого браузерного интерфейса — отдельный следующий этап. Не использовать реальные данные.

## Запуск проверяемой части

```bash
python -m pip install -r apps/api/requirements-dev.txt
PYTHONPATH=apps/api python -m pytest -q tests/bootstrap
python scripts/check_contract.py
PYTHONPATH=apps/api uvicorn roboport_api.main:app --host 127.0.0.1 --port 8000
```

Frontend после генерации и фиксации `package-lock.json`: `cd apps/web && npm ci && npm run build`. Пока CI не подтвердил запуск, эти команды являются **планом проверок**, а не свидетельством успешной сборки.

## Запреты текущего этапа

Никаких секретов, реальных персональных данных, внутренних адресов, сертификатов или ключей в Git. Нельзя подменять проверки реального PostgreSQL тестами SQLite, а настоящую локальную аутентификацию и ACL — synthetic fixtures. Изменения только через небольшие PR с исполняемыми тестами, журналом результатов и ссылками на Issues.


## Проверяемые ограничения модели данных (Issue #17)

Единая матрица `SQL → OpenAPI → ACL → статус` размещена в `docs/data/SCHEMA_FIELD_MATRIX.json`, архитектурные решения — ADR-032, согласованная карта FSM — `docs/domain/TICKET_TRANSITIONS.json`. SQL-поля со статусом `planned` **отсутствуют в реальной миграционной схеме** и не означают готовые API handlers. Основные договорённости: 20 состояний Ticket/FSM, право клиента на заявку фиксируется `service_ticket.customer_id`, версии SENT-смет и копейки immutable, совместимость OEM требует доказательства #18.

`db/legacy_drafts/*.sql` — шесть пустых исторических файлов, их **нельзя** выполнять как миграции. Для проверки используются `python scripts/check_contract.py`, `PYTHONPATH=apps/api python -m pytest -q tests/contract` и независимый PostgreSQL 17 CI. Публичный production release и реальные данные остаются заблокированы.
