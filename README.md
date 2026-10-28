# Airmatik — платформа управления автосервисом (разработка)

> **Единственная продуктовая цель (28.10.2026):** полностью автомобильная система уровня функционального охвата AutoDealer Online — организация/филиалы, клиенты/автомобили, запись, заказ-наряд, диагностика, склад, поставщики, CRM, деньги, отчёты и кабинет клиента. **Робототехнический домен выводится из целевой сборки** по контролируемому плану #165; его присутствие в текущих исходниках — миграционный долг, а не часть будущего продукта.

> **Статус:** работаем только с синтетическими данными в dev. Готовых production CRM, кассы, записи, заказ-наряда и полноценного автомобильного интерфейса пока нет. Публичный запуск, реальные ПДн и платежи запрещены до #119. Исторические robot API/таблицы/страницы временно остаются ради безопасного перехода и тестового восстановления; они не описывают новую бизнес-модель.

**Основные документы:** [Продукт и роли](docs/auto/PRODUCT.md) · [52 функции и владельцы задач](docs/auto/FUNCTIONAL_MATRIX.md) · [План этапов и сквозных тестов](docs/auto/FEATURE_ROADMAP.md) · [Архитектурное решение](docs/auto/ADR-001.md) · [Epic #107](https://github.com/askarahodov/roboport/issues/107).

## Локальный портал документации (dev)

[Инструкция запуска Docsify 5.0.0](docs/PORTAL_SETUP.md) — браузерные разделы по ролям, поиск, code blocks и проверка переходов через Playwright. Запуск в localhost, реальных пользовательских данных нет; клиентские инструкции со статусом planned не являются готовыми функциями.

## С чего начинать

1. [Карта документации](docs/INDEX.md), [политика актуальности](docs/DOCUMENTATION_POLICY.md), [результаты аудита](docs/auto/DOCUMENTATION_AUDIT.md).
2. **Автомобильная платформа Airmatik (цель; не готова):** [продукт](docs/auto/PRODUCT.md), [функции](docs/auto/FUNCTIONAL_MATRIX.md), [архитектура](docs/auto/ARCHITECTURE.md), [ADR](docs/auto/ADR-001.md), [каталог](docs/auto/PARTS_CATALOG.md).
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
