# Карта документации автомобильной платформы Airmatik Pro Service

> **Браузерная версия (dev):** [стартовая страница](README.md) · [навигация](PORTAL_SETUP.md) · [руководства по ролям](guides/INDEX.md). Для просмотра запустить локальный сервер как описано в PORTAL_SETUP.md. Не является публичным порталом.

**Дата продуктовой сверки:** 28.10.2026. Все смысловые документы ведём **на русском**. Код/API/HTTP/SQL/команды сохраняем на языке исходных интерфейсов.

Статусы: **current** — работает и проверяется; **legacy** — существующий домен робототехники; **planned** — новые возможности автосервиса, которых пока нет. Не смешивать.

## Для новой разработки Airmatik

| Документ | Статус | Назначение |
|---|---|---|
| [PRODUCT.md](auto/PRODUCT.md) | planned | Продуктовая концепция и сценарии автосервиса |
| [FUNCTIONAL_MATRIX.md](auto/FUNCTIONAL_MATRIX.md) | current specification, реализации planned | 50+ функций AutoDealer benchmark, current-dev/legacy/planned |
| [FEATURE_ROADMAP.md](auto/FEATURE_ROADMAP.md) | current roadmap | Зависимости #107–#174 и обязательный вывод robot legacy (#165) |
| [ARCHITECTURE.md](auto/ARCHITECTURE.md) | planned / частично current | Границы автомобильных модулей, безопасность |
| [ADR-AUTO-001](auto/ADR-001.md) | current decision | Решение о поэтапной миграции |
| [PARTS_CATALOG.md](auto/PARTS_CATALOG.md) | planned | Лицензированные OEM/aftermarket провайдеры |
| [DOCUMENTATION_AUDIT.md](auto/DOCUMENTATION_AUDIT.md) | current | Сверка фактической реализации и устаревших разделов |
| [Руководства по ролям](guides/INDEX.md) | planned | Администратор, приёмщик, механик, склад, руководитель, бухгалтер, клиент |
| [Политика документации](DOCUMENTATION_POLICY.md) | current | Обязательное обновление руководств в том же PR |

## Для разработчика / эксплуатации

| Документ | Статус | Назначение |
|---|---|---|
| [AGENTS.md](../AGENTS.md) | current | Инструкции AI-агентов и безопасная разработка |
| [Технологический стек](TECH_STACK.md) | current/legacy | Проверенные версии, инфраструктура, ограничения |
| [Окружение](operations/DEV_ENVIRONMENT.md) | current dev | Команды dev build, проверки, Windows/Linux |
| [Версии docs и dev-журнал](operations/DOCS_VERSIONS.md) | current dev | Provenance Git SHA ↔ CI, правила будущей публикации и откат |
| [LOCAL_STACK.md](../infra/dev/LOCAL_STACK.md) | current dev | Отдельный синтетический стек PostgreSQL/Keycloak/MinIO |
| [Правила API](api/API_IMPLEMENTATION.md) | current/legacy | Контракт, защита, правила новых endpoints |
| [Стандарт GitHub Issues](implementation/ISSUE_STANDARD.md) | current | Acceptance criteria + обязательный docs impact |
| [Структура репозитория](REPOSITORY_STRUCTURE.md) | частично legacy | Размещение исходников; плановые каталоги отделять от текущих |
| [Release gates](operations/SECURITY_RELEASE_GATES.md) | legacy/частично current | Не переносить в production без нового auto review |

## Архив существующего робототехнического домена (только до миграции #165)

Следующие источники **не являются** требованиями нового автосервиса, но остаются значимыми для безопасности и тестов существующих модулей:

- [TECHNICAL_SPEC.md](../TECHNICAL_SPEC.md) — legacy-спецификация ROBOport.
- [ADR существующего проекта](architecture/ADR.md) и [границы модулей](architecture/BOUNDARIES.md).
- [Модель данных](data/DATA_MODEL.md), [FSM](domain/TICKET_FSM.md) и [сверка схем](data/SCHEMA_RECONCILIATION.md).
- [Матрица тестов](quality/ACCEPTANCE_TESTS.md) и [модель безопасности](security/SECURITY_MODEL.md).
- [Исторический roadmap](implementation/DEVELOPMENT_ROADMAP.md) и [WBS](implementation/WORK_BREAKDOWN.md) — прежняя нумерация, НЕ активный backlog Airmatik.

**Актуальный backlog:** [автосервис #107](https://github.com/askarahodov/roboport/issues/107), [документация #121](https://github.com/askarahodov/roboport/issues/121).

## Порядок работы AI-агента

`Issue → AGENTS.md → auto PRODUCT/ADR (если auto), legacy TECHNICAL_SPEC (если robot) → контракт + существующие тесты → код → scoped tests → обновить документацию тех. и пользовательскую → PR/CI/review`.

Новый сервис **ещё не открыт** для реальных клиентов. Публикация навигационной базы знаний и проверка всех ссылок: [#125](https://github.com/askarahodov/roboport/issues/125).
