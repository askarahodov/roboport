# Модель данных — согласуемый черновик

**Эталон для миграции ещё не утверждён:** исторические SQL `db/legacy_drafts/001–006` НЕ исполнялись на PostgreSQL. Перед Alembic обязательна задача #17: сопоставить контракт API, FSM и таблицы.

## Клиенты и устройства

- `app_user` — локальный субъект; `local_credential` — Argon2id hash и политика пароля; `user_session` — hash случайной сессии и сроки/revocation; `role`, `user_role` и `acl_entry` — роли и явные права на объекты. `customer` — сторона заказа, `customer_account` — кабинет; `account_customer_link` — подтверждённая и отзывная связь; `account_identity` оставить для возможного внешнего IdP позже. Никакого самоприсвоения заявки по телефону/SN.
- `robot_device` — устройство с идентификатором/вариантом; `device_ownership` — временная история владения, не правило доступа ко всем старым заявкам.
- `service_ticket.customer_id` определяет заявителя конкретного заказа; его доступ к акту/сметам не следует автоматически за новым владельцем `robot_device`.

## Каталог и совместимость

- `brand`, `robot_model`, `model_variant`, `dock_variant` — точная модель, ревизия, регион и станция.
- `service_capability` и `part_compatibility` — **подтверждение**, с ответственными, датой, evidence и статусом; нельзя вывести `SUPPORTED` по совпадению бренда.
- `part`, `stock_lot`, `inventory_reservation`, `stock_movement` — P/N, партии и ACID-транзакции; остаток не может быть отрицательным даже при конкурентных запросах.

## Заявки, сметы и качество

- `service_ticket`, `ticket_state_event`, `diagnosis`, `safety_hold`, `work_log`, `qc_result` — guarded FSM и аудит.
- `estimate`, `estimate_line`, `estimate_approval` — идентификатор версии, frozen `SENT` строки и сумма, явное клиентское действие. `QC_FAILED` блокирует выдачу.

## Файлы и события

- `attachment` (метаданные файла и объектная привязка к приватному dev MinIO через `FileStorage`), `document_access_audit`, `audit_event` — запись доступа, append-only аудит.
- `notification_outbox`, `idempotency_request` — транзакционные события и обработка повторов. Не отправлять внешние сообщения в dev.

## Требования к DDL

UUID PK/FK; `timestamptz` UTC; денежные поля integer minor units; `NOT NULL/UNIQUE/CHECK/partial indexes`; ограничение единственной активной подтверждённой связи; индексы ticket/customer/owner/partition; запись аудита только добавлением; критические переходы с optimistic version/row lock. Каждое ограничение подтверждается **реальным** тестом на disposable PostgreSQL 17; SQLite этого не заменяет.

## Что должно появиться после #17/#11

Матрица `Сущность → поле → тип → constraint → владелец модуля → API schema → security guard → тест`, исполняемые Alembic-ревизии и журнал upgrade/downgrade/restore. Не выдумывать, что вся историческая схема уже готова.
