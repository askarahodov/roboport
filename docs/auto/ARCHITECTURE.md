# Airmatik — целевая архитектура

> **Статус: planned / partly current.** В main уже существует только SQL-основа автомобильной сущности (`auto_vehicle`, `auto_vehicle_customer_link` в миграции 0021). Запись на ремонт, auto API, workflow заказ-наряда, OEM и мобильное приложение пока запланированы. Текущее состояние сверять с [аудитом документации](DOCUMENTATION_AUDIT.md).

## Current
React 19, TypeScript, Vite; FastAPI/Python; PostgreSQL 17 + Alembic migrations 0001–0021; OpenAPI, RBAC/ACL, sessions/OIDC, MinIO, estimate versioning, inventory, outbox. Исходная предметная область — робототехника.

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
