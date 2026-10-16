# Реальный dev-срез смет (#20)

Только synthetic PostgreSQL + loopback HTTPS; публичный релиз заблокирован.

## HTTP

Все POST требуют живую HttpOnly сессию `rp_session`, доверенный `Origin`, `X-CSRF-Token`; backend никогда не принимает user/actor из тела.

| Метод | Маршрут | Назначение | Границы |
| --- | --- | --- | --- |
| POST | `/api/v1/dev/tickets/{ticket_id}/estimates` | Создать DRAFT с `lines: [{description, amountMinor}]` | MANAGER + точный ACL `estimate.approve` по ticket, только допустимое состояние FSM |
| POST | `/api/v1/staff/tickets/{ticketId}/estimates` | Контрактный запрос `currency`, `expectedTicketVersion`, `lines` | Только synthetic adapter, guarded manager/ACL/FSM |
| POST | `/api/v1/dev/estimates/{estimate_id}/send` | Зафиксировать `expectedVersion` | MANAGER + ACL; сумма равна сумме строк |
| POST | `/api/v1/dev/estimates/{estimate_id}/decision` | `decision: APPROVED/DECLINED`, `expectedVersion` | Только CLIENT, связанный с владельцем заявки; обязательный `Idempotency-Key` |

Ответы: `200` для send/decision, `201` для create, `401` без сессии, `403` Origin/CSRF, `404` без object-level доступа, `409` версия/state/fingerprint, `422` неверный запрос. `Cache-Control: no-store`.

## Инварианты

- Цена хранится целым `total_minor` и рассчитывается сервером по `estimate_line.amount_minor`.
- После SENT строки/сумма не меняются: enforcement триггеров PostgreSQL из baseline.
- Новая цена создаёт новую версию, после отправки старую SENT версию согласовать нельзя.
- `estimate_approval` фиксирует actor UUID, точную estimate UUID/версию косвенно и `created_at`, является append-only.
- Решение атомарно с `estimate_decision_receipt`. Replay идентичного fingerprint возвращает тот же результат, но только после повторной проверки живых сессии, роли и владения заявкой.
- Смена владельца устройства не передаёт прежние заявки: разрешение опирается на `account_customer_link` и неизменяемого `service_ticket.customer_id`.

## Граница реализации

Контрактные `/api/v1/customer/estimates/{estimateId}/approve|decline` и `/api/v1/staff/.../estimates` маршруты добавлены как синтетические совместимые HTTP-адаптеры к локальному ядру. Они, как и `/dev/*`, доступны **только** при `ROBOport_SYNTHETIC_ACCESS_API=1`, через HTTPS localhost, и не публикуются через генерацию FastAPI OpenAPI (`include_in_schema=False`). Полный публичный API продукта по-прежнему является проектным контрактом и не объявляется реализованным либо безопасным к публикации. Метод verified_signature намеренно не реализован без криптографической проверки подписи.

Проверка: одноразовая PostgreSQL 17, `pytest -q tests/integration tests/security` в CI, smoke API/web и browser E2E. Не отмечать production-ready.
