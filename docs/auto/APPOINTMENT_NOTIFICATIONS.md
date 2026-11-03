# Уведомления о записи — транзакционный synthetic outbox (#113)

> **current-dev, только disposable PostgreSQL / HTTPS loopback.** Никакие SMS, email, push, номера телефонов, адреса, реальные VIN или настоящие клиентские сообщения не отправляются. Production / privacy / consent / договоры с провайдерами остаются `planned` и заблокированы #119, #117 и #169.

## Реализованный контракт

1. Действующий мастер-приёмщик (`INTAKE` в своём филиале) создаёт запись через `/staff/auto-calendar`. Одновременно в транзакции базы фиксируются активный пост, `Appointment`, `WorkOrder`, событие `auto_appointment_event.CREATED` и отдельная строка `notification_outbox` (`auto_appointment / CREATED`).
2. При успешном переносе `auto_appointment_reschedule` фиксирует историю и создаёт **ровно одно** событие `RESCHEDULED`. Отмена прежней брони внутри переноса не создаёт ложное событие клиентской отмены.
3. При настоящей отмене актуальной брони `auto_appointment_event.BOOKING_CANCELED` создаёт `CANCELED`. Повторная отмена (`ALREADY_CANCELED`) не создаёт ещё одно событие.
4. При откате любой бизнес-транзакции её уведомление тоже откатывается. События генерирует БД, а не отдельный HTTP-вызов, поэтому успешное создание и запись в очередь атомарны.
5. Существующий `notification_outbox`/worker поддерживает claim `FOR UPDATE SKIP LOCKED`, lease/fencing, backoff 2/4/8/16 секунд, максимум 5 попыток и `DEAD`. Успех — только запись в **`synthetic_notification_delivery` (`DEV_SINK`)**, в одной транзакции с `DELIVERED`. Повтор worker не создаёт второй sink receipt.

`event_key` различает append-only идентификатор события: `auto-appointment:event:<event-id>` для создания/отмены и `auto-appointment:move:<reschedule-id>` для переноса. `aggregate_type='auto_appointment'`; `aggregate_id` — UUID записи; `event_type` — `CREATED`, `RESCHEDULED` либо `CANCELED`. JSON `payload` содержит **только** `{"action":"..."}`; никакого имени, телефона, VIN, customerLinkId, employeeId или пользовательского текста.

Существующие legacy `ticket`/`estimate` события и ограничения сохраняются. Миграция `0089_auto_appointment_outbox` добавляет только поддерживаемые типы, индекс и два триггера. Downgrade **не удаляет** уже созданные уведомления: при наличии `auto_appointment` receipts он останавливается и требует восстановления из изолированной копии.

## Как проверить на синтетическом стенде

На установленной disposable PG17 базе с миграциями используйте тесты `tests/integration/test_pg_auto_appointment_outbox.py`: создание → перенос → настоящая отмена дают ровно 3 события; повторная отмена — ноль дополнительных; rollback не оставляет phantom row; после одного искусственного отказа worker повторяет событие, а `DEV_SINK` фиксируется ровно один раз. Базовые проверки очереди, параллельных workers, lease expiry и `DEAD` находятся в `tests/integration/test_pg_notification_outbox.py`.

Для роли приёмщика **новых кнопок нет**: действия и подтверждение в календаре работают как раньше; запись в outbox выполняется автоматически. Dev sink — доказательство транзакционного сценария, **не** факт информирования клиента. Продублированные ключи `Idempotency-Key` не создают дублей. Нельзя помещать секреты и настоящие данные клиентов в payload, логи или GitHub артефакты.

## Что остаётся до полного #113 / production

Согласие на сообщения, шаблоны и язык, доверенный канал/провайдер, opt-out, юридический статус доставки, долговременное хранение, timezone филиала, расписание в локальном времени, самостоятельная клиентская запись, услуги и материалы внутри атомарного appointment flow, а также security/privacy/operations release gate #119. Нельзя закрывать #113 на основании одного dev outbox.


## Материалы блокируют отмену записи (#113, current-dev)

Если по заказ-наряду есть **незакрытый складской резерв** или **хотя бы одна фактическая выдача**, система **не отменяет запись**: транзакционная защита `auto_booking_material_cancel_guard` останавливает прямой SQL update и запрос staff API получает `409 STOCK_RECONCILIATION_REQUIRED`. Роль CLIENT не может самостоятельно отменить или перенести такую запись (`403`, `canManage:false`). Отсутствуют ложные события `CANCELED` в outbox. Когда есть **только RESERVE**, кладовщик с ролью `WAREHOUSE` может вернуть всю сумму через `RELEASE`, после чего отмена доступна. После `ISSUE` нужна отдельная оформленная процедура возврата/разбирательства с материалами: простого `RELEASE` недостаточно. Технический перенос не является отменой; он сохраняет заказ и резерв.

Миграция `0091_auto_booking_stock_guard` также обновляет функцию `record_auto_work_order_stock`: после переноса она учитывает **актуальную** бронь из `auto_appointment_reschedule`, а не старый `auto_appointment.booking_id`. Проверки: `tests/integration/test_pg_auto_booking_stock_guard.py`, `tests/integration/test_pg_auto_work_order_stock.py`. Это защита целостности synthetic склада, **не** реальная закупка, складское списание с юридическим актом или внешнее уведомление.
