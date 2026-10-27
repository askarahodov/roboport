# Airmatik — целевая архитектура

> **Статус: planned / partly current.** В main уже существует только SQL-основа автомобильной сущности (`auto_vehicle`, `auto_vehicle_customer_link` в миграции 0021). Запись на ремонт, auto API, workflow заказ-наряда, OEM и мобильное приложение пока запланированы. Текущее состояние сверять с [аудитом документации](DOCUMENTATION_AUDIT.md).

## Current
React 19, TypeScript, Vite; FastAPI/Python; PostgreSQL 17 + Alembic migrations 0001–0024; OpenAPI, RBAC/ACL, sessions/OIDC, MinIO, estimate versioning, inventory, outbox. Исходная предметная область — робототехника.

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

Операции доступны клиенту только при активной cookie-сессии, роли CLIENT, подтверждённой (`verified_at` и `verified_by`) и неотозванной связи автомобиля и существующей `account_customer_link`. Само знание VIN или новый pending link доступа не даёт. Проверка роли, сессии и связи выполняется в одном SQL запросе: A/B изоляция, отзыв связи/роли/сессии подтверждаются интеграционными тестами. Автомобильный write API, staff review, OpenAPI/SDK и пользовательский интерфейс **planned** и остаются в #112.

## Подтверждение клиентской связи с машиной (dev milestone #130)

Аддитивная миграция `0022_auto_vehicle_claim` содержит одноразовые доказательства и отдельные staff object assignments `auto_vehicle_staff_assignment` для ролей `INTAKE`/`MANAGER`. Доказательство выдаёт назначенный мастер-приёмщик, предъявляет клиент с действующей `account_customer_link`, подтверждает независимо назначенный менеджер. Все мутации требуют cookie-сессию + same-origin/CSRF, производятся в транзакции и создают append-only audit; повторное подтверждение запрещено. После отзыва связи список автомобилей скрывает запись, старая история заявок робототехнического домена не передаётся.

Эта схема реализована **только в закрытом synthetic HTTPS loopback API**, филиалы и customer web UI пока не реализованы; полномочия сотрудников на конкретный автомобиль создаются лишь dev-fixtures. Подробнее: [VEHICLE_OWNERSHIP.md](VEHICLE_OWNERSHIP.md). Публичный и production API будут отдельно рассмотрены в #131/#119.
