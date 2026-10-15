# Жизненный цикл сервисной заявки (FSM)

## Состояния (проектный набор)

`DRAFT`, `SUBMITTED`, `TRIAGE`, `AWAITING_DELIVERY`, `RECEIVED`, `DIAGNOSING`, `SAFETY_HOLD`, `ESTIMATE_DRAFT`, `AWAITING_APPROVAL`, `APPROVED`, `DECLINED`, `AWAITING_PARTS`, `REPAIRING`, `QC_PENDING`, `QC_FAILED`, `READY_FOR_HANDOVER`, `COMPLETED`, `CANCELLED`, `UNREPAIRABLE`, `WARRANTY_ROUTE`.

Имена должны быть **сопоставлены с проектными enum SQL и OpenAPI в #17**. Пока это не означает, что все переходы реализованы.

## Переходы и запреты

- Новый заказ: `DRAFT → SUBMITTED → TRIAGE → AWAITING_DELIVERY → RECEIVED → DIAGNOSING` (пропуски возможны только через явный ADR/guard).
- При опасности: `DIAGNOSING → SAFETY_HOLD`; из hold нельзя разрешать ремонт, работы и выдачу без отдельного экспертного подтверждения.
- Смета: `DIAGNOSING → ESTIMATE_DRAFT → AWAITING_APPROVAL → APPROVED | DECLINED`; изменение цены после отправки создаёт **новую версию сметы** и повторное согласие.
- Ремонт: `APPROVED → AWAITING_PARTS | REPAIRING → QC_PENDING → READY_FOR_HANDOVER → COMPLETED`; `QC_FAILED` направляет в повторные работы, не в выдачу.
- `CANCELLED/UNREPAIRABLE/WARRANTY_ROUTE` — специальные решения с отдельными правами и письменной причиной; переходы не добавлять произвольно.

## Инварианты транзакции

Каждый переход задаётся действием/событием и ожидаемой версией (optimistic concurrency), а не свободным `PATCH status`. Проверяется роль, конкретная заявка, текущая версия, safety, состояние сметы, QC, физический получатель. Успех создаёт событие и аудит в той же транзакции; конфликт → `409 Error`, скрытый чужой объект → безопасный отказ. Время в UTC, PII не включать в лог.

## Тесты

Проверять как минимум: недопустимый переход, параллельный переход, bypass safety hold, выдачу после QC FAIL, повторный approve устаревшей сметы, запрет доступа другого клиента. Реальная PG проверка обязательна, UI-кнопка `disabled` её не заменяет.
