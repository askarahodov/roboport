# Airmatik Pro Service — архитектура полностью автомобильной платформы

> **Статус: current только для синтетического dev / production planned.** Реализованы схема авто, подтверждение связи, приватное чтение и экран `/account/vehicles`. Публичный автомобильный API, запись на ремонт, заказ-наряды, OEM и мобильное приложение пока запланированы. Исторический [аудит документации](DOCUMENTATION_AUDIT.md) датирован 26.10.2026.

## Current
React 19, TypeScript, Vite; FastAPI/Python; PostgreSQL 17 + Alembic migrations 0001–0032; OpenAPI, RBAC/ACL, sessions/OIDC, MinIO, estimate versioning, inventory, outbox. Исходная предметная область — робототехника.

## Decision
Модульный монолит и Strangler Pattern. Существующие таблицы и API не переименовывать и не удалять до регрессионного доказательства; автомобильные контексты добавлять отдельными модулями и миграциями. Основание: ADR-001.md.

## Контексты
- Organization/ServiceLocation: филиал, часы, смены, посты, доступ сотрудника.
- Customer/Vehicle: auto_vehicle + pending customer link. Доступ к автомобилю допускается только по явно проверенной, неотозванной связи; verified_at и verified_by требуют полномочного staff-review в будущем API. Связь машины с клиентом не передаёт старые наряды.
- Scheduling: Appointment, WorkBay, исключение пересечений при конкурентной записи.
- WorkOrder: автомобильная приёмка, Inspection, Labor, Estimate, QC.
- Parts: OEM/aftermarket провайдер, применяемость, склад, резерв, закупки.
- Documents/Notifications: приватный FileStorage, audit, outbox.
- Auth: роли + ACL на конкретном заказе/филиале/автомобиле, deny by default.

## API/Mobile
Автомобильные эндпоинты проектировать под /api/v1/auto/, с versioning, pagination, стабильными ошибками и OpenAPI. Browser session не является обязательным единственным вариантом мобильной авторизации; решения о native flows только после security ADR. Не отправлять API keys каталога на клиент.

## Инварианты
VIN nullable, нормализованный uppercase 17 символов из разрешённого набора, без утверждений о глобальной валидности по одному regex. История заказа принадлежит заказчику и не переходит автоматически новому владельцу автомобиля. Отправленная смета immutable; нельзя резервировать больше остатка; отзывы доступа действуют и при replay. Источник внешней детали, версия каталога и время обязательны. В dev только synthetic.

## Последовательность
#108/109 продукт и ADR → #112 автомобильный registry → #113 расписание → #114 заказ-наряд → #111/115 catalog/stock → #116/117 CRM и кабинет → #118 mobile → #119 release gate.

## Rollback
Аддитивные миграции вверх/вниз проверяются только на disposable PG17. Деструктивный downgrade production невозможен без сохранения данных, backup/restore и согласованного окна. Старые таблицы пока остаются на месте.

## Первый read-only dev API автомобилей (#112)

После отдельного среза для тестирования введены два **приватных синтетических** endpoint: `GET /api/v1/dev/auto/vehicles` и `GET /api/v1/dev/auto/vehicles/{vehicle_id}`. Они не входят в публичный OpenAPI и не считаются готовым клиентским функционалом. Для запуска требуется `ROBOport_SYNTHETIC_AUTO_API=1`, активный синтетический auth `ROBOport_SYNTHETIC_AUTH_API=1`, локальный PostgreSQL и HTTPS loopback. Ответы `Cache-Control: no-store` и только последние четыре символа VIN.

Операции доступны клиенту только при активной cookie-сессии, роли CLIENT, подтверждённой (`verified_at` и `verified_by`) и неотозванной связи автомобиля и существующей `account_customer_link`. Само знание VIN или новый pending link доступа не даёт. Проверка роли, сессии и связи выполняется в одном SQL запросе: A/B изоляция, отзыв связи/роли/сессии подтверждаются интеграционными тестами. Первоначальный read-only срез расширен последующими dev-only этапами: proof/claim review (#130), клиентским UI и отдельным OpenAPI/TS SDK (#131). **Публичные** операции и production authorization остаются planned.

## Подтверждение клиентской связи с машиной (dev milestone #130)

Аддитивная миграция `0022_auto_vehicle_claim` содержит одноразовые доказательства и отдельные staff object assignments `auto_vehicle_staff_assignment` для ролей `INTAKE`/`MANAGER`. Доказательство выдаёт назначенный мастер-приёмщик, предъявляет клиент с действующей `account_customer_link`, подтверждает независимо назначенный менеджер. Все мутации требуют cookie-сессию + same-origin/CSRF, производятся в транзакции и создают append-only audit; повторное подтверждение запрещено. После отзыва связи список автомобилей скрывает запись, старая история заявок робототехнического домена не передаётся.

Эта схема реализована **только в закрытом synthetic HTTPS loopback API**, production филиалы и bootstrap MANAGER пока не реализованы; полномочия сотрудников на конкретный автомобиль создаются лишь dev-fixtures. Подробнее: [VEHICLE_OWNERSHIP.md](VEHICLE_OWNERSHIP.md). Публичный и production API будут отдельно рассмотрены в #131/#119.

## Клиентский просмотр автомобилей (UI checkpoint #131)

В существующем синтетическом клиентском кабинете добавлен маршрут `/account/vehicles` («Автомобили (dev)»). Компонент `CustomerVehicles` вызывает только `GET /api/v1/dev/auto/vehicles` с серверной HttpOnly cookie, после проверки авторизации существующего `CustomerAccount`. Он различает loading, отсутствие подтверждённых связей, сетевую ошибку и отозванный доступ. Показываются только маска VIN, марка, модель и доступные неидентифицирующие поля. Экран не позволяет записывать/редактировать автомобили и не считается production public API. Доступность на 390/1280, цвета Light/Dark и retry проверяются Playwright. Публичный OpenAPI и mobile auth остаются запланированными до security ADR и #119.


### Пагинация клиентского dev-списка (#131)

`/account/vehicles` загружает по 20 подтверждённых автомобилей через `?limit=20&offset=N` (предел offset в текущем dev API — 10000). При каждой подгрузке страница повторно читает все ранее отображённые порции с offset=0, заново сверяет текущую доступность и заменяет набор карточек: backend возвращает **200 с пустым списком** после отзыва `account_customer_link`, поэтому нельзя полагаться только на 401/403/404. Повторные идентификаторы не дублируются; техническая ошибка следующей страницы сохраняет предыдущий успешный набор, retry снова проверяет все видимые порции. 401/403/404 очищают все отображённые карточки. Кнопка общего обновления сбрасывает список и перепроверяет действующий доступ. Пагинация остаётся **dev-only**: перед production нужны стабильный keyset cursor и контракт с признаком следующей страницы, security review и #119.


## Автомобильный dev OpenAPI и TypeScript SDK (#131)

Для двух действующих read-only GET операций добавлен **отдельный** `contracts/auto-dev-openapi.yaml` версии `0.1.0-dev` с `x-synthetic-only: true` и сервером **только HTTPS loopback**. Схемы AutoVehicle, AutoVehiclePage, AutoDevError проверяются независимо и сверяются с реальными HTTP-ответами PostgreSQL; полный VIN отсутствует. Клиентский `autoDevClient.ts` использует OpenAPI-generated типы и runtime-проверку JSON, same-origin cookie credentials, no-store; генерация из YAML выполняется до dev/typecheck/build и файл результата не сохраняется в git. Новая схема **не** расширяет публичный `contracts/openapi.yaml` (legacy/robot contract 46 операций) и не включает автомобильный dev API в FastAPI `/openapi.json`. Это QA-предпосылка для будущего `/api/v1/auto/*`, а не production approval. Переход на cursor pagination и мобильную аутентификацию остаётся blocked до #119.


### Docker build guard (#131)

В `apps/web/Dockerfile` генератор TypeScript читает `../../contracts/auto-dev-openapi.yaml` относительно `/app`, то есть `/contracts/auto-dev-openapi.yaml` внутри builder stage. Dockerfile явно копирует этот файл из корневого build context, а CI собирает `docker build --target builder -f apps/web/Dockerfile .` для проверки реальной сборки образа. `docker-compose.yml` остаётся legacy/dev-конфигурацией: успешная сборка **не означает**, что синтетический HTTPS loopback API безопасно доступен из контейнера или пригоден для production.

## Управление object-scoped INTAKE в dev (#130)

Миграция `0025_auto_intake_delegation` добавляет append-only audit выдачи и отзыва назначения роли INTAKE. Приватный HTTPS loopback API позволяет только уже назначенному MANAGER делегировать и отзывать INTAKE **на том же автомобиле**; менеджерский grant, глобальный `user_role` и филиалы через API не изменяются. SQL-блокировка автомобиля сериализует конкурентные выдачи, отзыв необратим, audit без PII. Для production требуются независимые authority grants, организация/филиалы, правовая процедура и #119.

## Proof recovery без хранения секрета (#130 / #147)

Новая `0029_auto_proof_reissue` добавляет таблицу с единственным неизменяемым replacement proof по claim UUID, без изменения исходного `auto_vehicle_claim`, его исходного хеша и истории аудита. Триггер аудита допускает новое append-only событие REISSUED. API повторной выдачи привязан к исходному `link_id` (доступному даже если первый HTTP-ответ потерян), прежнему issuing INTAKE, object scope, CSRF и глобальному issue throttle. Первая попытка предъявления/решение/отзыв связи необратимо запрещают recovery. При предъявлении секрет выбирается отдельным запросом **после блокировки claim** — это устраняет READ COMMITTED race в момент перевыпуска.  Производственный процесс владения остаётся blocked.

## Решение заказчика от 28.10.2026 — единственная цель: автомобили и СТО

Старый robot domain **исключён из целевой архитектуры**. Задача [#165](https://github.com/askarahodov/roboport/issues/165) завершает инвентаризацию robot UI/API/DDL/ролей, проверяемую миграцию, cutover, архив/restore и decommission. Наличие старого `service_ticket`, `inventory`, `estimate`, `customer`, `robot_device` означает только *legacy код*, пока для каждой операции не создана и не проверена автомобильная семантика. Правило «временно хранить старые таблицы» **не** разрешает оставлять робототехнический продукт в конечном интерфейсе или публикуемом API.

Канонические бизнес-сценарии и полная матрица возможностей — [продукт](PRODUCT.md), [матрица AutoDealer](FUNCTIONAL_MATRIX.md), [дорожная карта](FEATURE_ROADMAP.md). Новые bounded contexts: Organization/Location (#166), Vehicle/Customer (#112/#130), Appointment/WorkBay (#113), WorkOrder/Inspection/Labor/Estimate/QC (#114/#167), Catalog/Purchase/Stock Ledger (#111/#115/#168), CRM (#169), Payments/Documents (#170), Analytics/Payroll (#171), Import/Cutover (#172/#165) и Client/Mobile (#117/#118). Не путать dev-макет автосвязи с готовой организацией или действующей клиентской историей.

Автомобильный MANAGER, выданный двумя независимыми ADMIN в closed dev bootstrap (#163, миграция `0032`), не получает глобальный `user_role MANAGER`, который открыл бы robot `guest-claims` и `ticket.manage`. Проверка auto роли связана с одобренной заявкой, vehicle assignment и активным филиальным grant. Это временный synthetic security pilot, не bootstrap реальной организации.

Для production нужны авторизация для нескольких организаций, реальный tenant boundary, trusted ingress/peer, подтверждение законных владельцев/согласий, retention, лицензии OEM/нормочасов и финансовая нормативная проверка. До этого выпуск блокируется [#119](https://github.com/askarahodov/roboport/issues/119). Финальный target build обязан иметь только автомобильные UI, API и документы, что проверяется отдельным cutover E2E и reconciliation при миграции.
