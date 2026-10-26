# ROBOport → Airmatik Pro Service — постепенный переход к платформе автосервиса

> **Статус: разработка на синтетических данных.** Текущий код преимущественно обслуживает робототехнику (legacy); автомобильный сервис ещё не готов. Первая схема автомобилей присутствует только в PostgreSQL. **Новая продуктовая цель (26.10.2026):** переход к системе автосервиса Airmatik Pro Service. [Продукт](docs/auto/PRODUCT.md), [архитектура](docs/auto/ARCHITECTURE.md), [матрица](docs/auto/FUNCTIONAL_MATRIX.md), [ADR](docs/auto/ADR-001.md), [каталоги 2026](docs/auto/PARTS_CATALOG.md), [Epic #107](https://github.com/askarahodov/roboport/issues/107). Ниже описана существующая робототехническая реализация, которая не является готовым автосервисом. Миграция поэтапная; dev — только на синтетических данных.

> **Состояние:** подготовка разработки; реальные клиенты, персональные данные, платежи и публичный запуск пока не допускаются. Этот репозиторий публичный.

ROBOport — единая система независимого мультибрендового сервиса домашних роботов: публичный сайт и каталог, **обязательный личный кабинет клиента**, рабочее место сотрудников (CRM), заявки, диагностика, сметы, контроль совместимости деталей, склад, контроль качества, документы и аудит.

## С чего начинать

1. [Карта документации](docs/INDEX.md), [политика актуальности](docs/DOCUMENTATION_POLICY.md), [результаты аудита](docs/auto/DOCUMENTATION_AUDIT.md).
2. **Автосервис Airmatik (planned):** [продукт](docs/auto/PRODUCT.md), [функции](docs/auto/FUNCTIONAL_MATRIX.md), [архитектура](docs/auto/ARCHITECTURE.md), [ADR](docs/auto/ADR-001.md), [каталог](docs/auto/PARTS_CATALOG.md).
3. **Текущий robot domain (legacy):** [историческое ТЗ](TECHNICAL_SPEC.md), [модель данных](docs/data/DATA_MODEL.md), [состояния заявок](docs/domain/TICKET_FSM.md).
4. [Технологический стек](docs/TECH_STACK.md), [окружение](docs/operations/DEV_ENVIRONMENT.md), [локальный стенд](infra/dev/LOCAL_STACK.md).
5. [Роли и будущие пользовательские руководства](docs/guides/INDEX.md), [AGENTS.md](AGENTS.md), [шаблон задач](docs/implementation/ISSUE_STANDARD.md).
6. [Главный эпик Airmatik #107](https://github.com/askarahodov/roboport/issues/107) и [эпик документации #121](https://github.com/askarahodov/roboport/issues/121) — актуальный backlog.

## Текущее состояние исходников (проверено 26.10.2026)

- **Backend:** FastAPI, PostgreSQL 17, миграции Alembic 0001–0021, серверные локальные сессии/OIDC, RBAC/ACL, заявки роботов, сметы, склад, документы и outbox. Это не готовая автомобильная CRM.
- **Автомобили:** только аддитивные таблицы `auto_vehicle` и `auto_vehicle_customer_link` с обязательной проверкой связи; автомобильный REST/UI ещё не реализован.
- **Frontend:** React 19, TypeScript 5, **Vite 7.1.12**, React Router 7, существующие public/customer/staff интерфейсы для робототехники. `apps/web/package-lock.json` присутствует.
- **Контракты:** `contracts/openapi.yaml` и сгенерированные типы. Наличие схемы не означает, что каждый маршрут существует.
- **Dev:** PostgreSQL/MinIO/Keycloak и synthetic fixtures; тесты API, PostgreSQL, web и Playwright проходят в GitHub Actions (например [PR #120](https://github.com/askarahodov/roboport/pull/120)).
- **Новая цель:** модели автомобиля, онлайн-запись, посты, заказ-наряды, лицензированные OEM-справочники и клиентский mobile API согласно #107–#119.

## Технический стек

Python 3.13 · FastAPI · PostgreSQL 17 · Alembic · React 19 · TypeScript 5 · Vite **7.1.12** · Node.js 24 · pytest · Playwright · GitHub Actions. Версии сверять с lockfile, а ограничения — с [docs/TECH_STACK.md](docs/TECH_STACK.md). Это синтетическая разработка, не production release.

## Воспроизводимое локальное развёртывание

Для PostgreSQL 17, Keycloak и MinIO используйте [локальный dev-стенд](infra/dev/LOCAL_STACK.md): `infra/dev/compose.local.yml` и `infra/dev/bootstrap-local.sh`. Стенд проверяется реальным CI `ROBOport local stack smoke`; API и frontend запускаются на хосте для сохранения loopback-only защитных проверок. Полный HTTPS reverse proxy для объединённого браузерного интерфейса — отдельный следующий этап. Не использовать реальные данные.

## Запуск проверяемой части

```bash
python -m pip install -r apps/api/requirements-dev.txt
PYTHONPATH=apps/api python -m pytest -q tests/bootstrap
python scripts/check_contract.py
PYTHONPATH=apps/api uvicorn roboport_api.main:app --host 127.0.0.1 --port 8000
```

Frontend: `cd apps/web && npm ci && npm run build`. Lockfile уже отслеживается Git; CI действительно проверяет build и браузерные E2E. Для полного PostgreSQL/Keycloak dev setup читайте `infra/dev/LOCAL_STACK.md`.

## Запреты текущего этапа

Никаких секретов, реальных персональных данных, внутренних адресов, сертификатов или ключей в Git. Нельзя подменять проверки реального PostgreSQL тестами SQLite, а настоящую локальную аутентификацию и ACL — synthetic fixtures. Изменения только через небольшие PR с исполняемыми тестами, журналом результатов и ссылками на Issues.


## Проверяемые ограничения модели данных (Issue #17)

Единая матрица `SQL → OpenAPI → ACL → статус` размещена в `docs/data/SCHEMA_FIELD_MATRIX.json`, архитектурные решения — ADR-032, согласованная карта FSM — `docs/domain/TICKET_TRANSITIONS.json`. SQL-поля со статусом `planned` **отсутствуют в реальной миграционной схеме** и не означают готовые API handlers. Основные договорённости: 20 состояний Ticket/FSM, право клиента на заявку фиксируется `service_ticket.customer_id`, версии SENT-смет и копейки immutable, совместимость OEM требует доказательства #18.

`db/legacy_drafts/*.sql` — шесть пустых исторических файлов, их **нельзя** выполнять как миграции. Для проверки используются `python scripts/check_contract.py`, `PYTHONPATH=apps/api python -m pytest -q tests/contract` и независимый PostgreSQL 17 CI. Публичный production release и реальные данные остаются заблокированы.
