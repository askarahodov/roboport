# Отдельный архив робототехнической документации (legacy)

> **Статус:** `legacy`, **не** рабочая инструкция автомобильной СТО.
> Документы сохранены для аудита, технического обслуживания старой системы
> и будущей миграции. Публичные и внутренние robot HTTP пути выведены из рабочего ASGI, но
> legacy SQL/вложения и production cutover остаются gate #165.

## Что относится к старому продукту

| Старая область | Документы репозитория | Назначение после перехода |
|---|---|---|
| Предметная область робототехнических обращений | [TECHNICAL_SPEC.md](../../TECHNICAL_SPEC.md), [FSM](../domain/TICKET_FSM.md) | Только исторический контракт, не модель автомобильного WorkOrder |
| Старые переходы, заявки и оборудование | [Сценарии RP](../domain/RP_007_008_ACCEPTANCE.md), [переходы](../domain/TICKET_TRANSITIONS.json) | Сопоставление и риски миграции, не новое руководство |
| Схема старой БД | [DATA_MODEL.md](../data/DATA_MODEL.md), [SCHEMA_RECONCILIATION.md](../data/SCHEMA_RECONCILIATION.md) | Аудит зависимости, не полная схема автомобиля |
| Старые архитектурные решения | [ADR.md](../architecture/ADR.md), [BOUNDARIES.md](../architecture/BOUNDARIES.md) | Только контекст существующего кода |
| Старый backlog | [DEVELOPMENT_ROADMAP.md](../implementation/DEVELOPMENT_ROADMAP.md), [WORK_BREAKDOWN.md](../implementation/WORK_BREAKDOWN.md) | Исторические планы, не приоритеты СТО |
| QA старых клиентских и складских функций | [ACCEPTANCE_TESTS.md](../quality/ACCEPTANCE_TESTS.md), [INVENTORY_DEV_GATES.md](../quality/INVENTORY_DEV_GATES.md) | Отрицательные регрессионные проверки до cutover |
| Старая авторизация/вложения | [SECURITY_MODEL.md](../security/SECURITY_MODEL.md), [LOCAL_AUTH_AND_MINIO.md](../security/LOCAL_AUTH_AND_MINIO.md) | Сохранить до независимого security review |

Публичные robot-страницы `/robots`, `/catalog/*`, `/request-demo`, `/account/claim` исключены из React маршрутизации в автомобильном UI cutover; `/account/robots`, `/account/tickets`, `/account/estimates`, `/account/documents` больше не являются страницами клиентского кабинета; `/staff` открывает автомобильные рабочие разделы. Неиспользуемые React-компоненты и клиентский SDK старой robot-системы удалены из исходников в рамках frontend source cleanup (#165): `PublicCatalog`, `GuestIntake`, `CustomerTickets`, `CustomerSummary`, `EstimateActions`, `StaffGuestClaims`, `TicketWorkflow`, `StaffOperations`, `StaffAudit`, `catalogClient`. Старые UI-маршруты остаются отключены. Регрессионный guard — `tests/bootstrap/test_retired_robot_web_sources.py`: запрещает возвращение удалённых компонентов и вызов retired robot API из браузерного исходного кода. Исторические версии доступны в Git, но их нельзя включать в сборку. Legacy HTTP существует лишь в изолированном тестовом `tests/legacy_app.py`,
а исторические OpenAPI и SQL ещё остаются до decommission-gate. Это **не** означает, что они являются частью
новой автомобильной информационной системы. Не переименовывать сущность
`service_ticket` в автомобильный `WorkOrder` без новой модели прав и
согласий; не переносить историю по совпадению VIN. Робототехнические
инструкции не показываются в **актуальном пользовательском меню** Docsify.

## Разбор закрытых legacy PR

Шесть закрытых без merge веток (#1, #45, #46, #47, #59, #60) разобраны в [матрице решений по legacy PR](LEGACY_PR_DISPOSITION.md). Принципы, уже существующие в `main`, отделены от непереносимых robot-моделей. Аудит не означает cutover или разрешение production.

## Воспроизводимый снимок маршрутов (без удаления)

Запустите из корня репозитория `python scripts/inventory_legacy_routes.py --legacy-only > /tmp/roboport-legacy-routes.json`. Анализатор статически читает декораторы FastAPI и буквальные React Route/NavLink, выводит пути, исходные файлы, строки, признак скрытия из OpenAPI и кандидатов в legacy. Он **не** импортирует backend, не подключается к БД и не открывает клиентов. Все записи имеют `manual-required`: это список проверки, а не доказательство достижимости или разрешение на удаление. Динамические маршруты, нестандартные JSX выражения, mounts/prefixes при include_router и авторизацию требуется проверять отдельно в runtime. Регрессия: `python -m pytest -q tests/bootstrap/test_legacy_route_inventory.py`.

## Граница UI cutover (09.11.2026)

Автомобильный homepage, customer overview и staff workspace не вызывают старые robot API/queue; в E2E проверяются закрытые URL, роль, запрет старых вызовов и адаптивность 390/1280. **Это не физический backend decommission:** исторический draft `contracts/openapi.yaml` и legacy SQL ещё требуют миграции/архивирования; устаревшие HTTP методы уже вынесены из `main.py` в test-only harness. До независимой проверки не утверждаем, что все frontend source-файлы очищены от слова «робот».

## Выключение публичного robot API (после UI cutover)

На текущем runtime FastAPI намеренно **не регистрируются** `GET /api/v1/catalog/brands`, `GET /api/v1/catalog/models`, `GET /api/v1/catalog/models/{modelId}`. Запросы любых методов к этим адресам отвечают 404, а `/openapi.json` не включает robot catalog; соответствующая негативная bootstrap-проверка — `tests/bootstrap/test_public_catalog.py`. Текущий `/api/v1/meta` возвращает `Airmatik`. Это не означает удаления исторического `contracts/openapi.yaml`: draft контракт ещё содержит старый домен и должен быть либо заменён новой версией после contract review, либо окончательно архивирован, до того он **не** публичный контракт работающего автомобильного API. Внутренние legacy `/api/v1/dev/tickets`, `/api/v1/dev/catalog`, `/api/v1/dev/guest-claims`, старые staff queue/audit больше не регистрируются в рабочем ASGI. Для контрольной регрессии сохранён только тестовый архив.

## Что ещё нужно для настоящего вывода из эксплуатации

1. Инвентаризировать каждый legacy URL, React route, OpenAPI operation,
   таблицу, документ, систему хранения и внешнее подключение.
2. Установить доказанные эквиваленты в автомобильной модели и тесты,
   что данные каждого пользователя доступны только законному заказчику.
3. Выполнить реальные `pg_dump/pg_restore` и сценарии reversible cutover
   с контрольными суммами, provenance и точкой отката.
4. Получить одобрения business, security, privacy и legal в #119/#165.
5. Лишь после этого поэтапно убрать legacy-домен из приложения, меню и
   документации. До тех пор удаление или «тихая миграция» запрещены.

## Источник правды и рабочие руководства

Для действующих автомобильных сценариев открывайте
[быстрый старт ролей](../guides/QUICK_START.md),
[матрицу доступов](../guides/ACCESS_AND_TEST_MATRIX.md) и
[автомобильную дорожную карту](FEATURE_ROADMAP.md).
Актуальный приоритет деактивации/архивирования — [#165](https://github.com/askarahodov/roboport/issues/165),
документы и QA — [#173](https://github.com/askarahodov/roboport/issues/173).

## Финальный план вывода

См. [план полного вывода robot-domain](LEGACY_EXIT_PLAN.md): runtime и contract gates, проверка архивов, restore, безопасность, approvals #119 и отдельный SQL decommission. Текущий cutover **не** разрешает удаление исторических данных.
