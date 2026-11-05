
# Задачи обратной связи CRM (current-dev, #169)

> **Только synthetic HTTPS loopback + PostgreSQL 17.** Никаких звонков, SMS, email, мессенджеров, webhook и реальных уведомлений. Запись задачи не означает согласие клиента на контакт; персональные данные и сообщения не сохраняются. Production требует отдельного legal/security/ops gate #119.

## Мастер-приёмщик и руководитель: полный путь

1. Откройте `/staff/auto-crm-leads` с действующей ролью `INTAKE` или `MANAGER` своего филиала. Выберите уже существующее тестовое CRM-обращение, у которого сохраняется подтверждённая связь клиент–автомобиль. INTAKE видит только свои обращения, MANAGER — обращения филиала.
2. Под журналом стадий выберите тип задачи: `CALLBACK_NOT_SENT` (условное намерение перезвонить **без звонка**), `CHECK_DOCUMENTS`, `CONFIRM_BOOKING` или `SERVICE_REVIEW_NOT_SENT`. Выберите локальный день срока от текущей даты филиала до 365 суток вперёд и нажмите **«Добавить задачу»**.
3. Задача `OPEN` наследует ответственного CRM-обращения. Записывается `due_on` как локальная дата филиала, а не UTC timestamp сервера. Для пары `lead_id,kind,due_on` может существовать только одна запись. Повтор POST с тем же `requestKey` и телом возвращает `replayed:true`; новый ключ для дублирующего содержания — `409`.
4. Для ручного результата нажмите **«Выполнено»** (статус `DONE`) или выберите стандартную причину `NO_LONGER_NEEDED`, `DUPLICATE`, `OTHER` и нажмите **«Отменить задачу»** (`CANCELED`). Этим фиксируется решение сотрудника, а не подтверждённый телефонный разговор/исполнение клиентом. Оба состояния терминальные, второе завершение запрещено.
5. Если сеть прервалась после POST, нажмите **«Повторить тот же запрос»**, не меняя ключ, или сначала обновите журнал. При `409` обновите задачи и проверьте, не выполнена ли операция. При `422` исправьте тип, срок, причину. При `403` перепроверьте живую роль, tenant/location и verified proof; `404` — dev feature gate выключен. Смена филиала очищает прежний список.

## Источники истины

Миграция `0099_auto_crm_followups.py` добавляет:
- `auto_crm_followup_task`: immutable запись с `lead_id`, наследуемым `owner_id`, `actor_id`, `kind`, `due_on`, `request_key UNIQUE`, `UNIQUE(lead_id,kind,due_on)`. PostgreSQL trigger проверяет действующего ответственного с филиальной ролью INTAKE/MANAGER.
- `auto_crm_followup_event`: append-only событие DONE/CANCELED, actor, timestamp, `request_key UNIQUE`, `UNIQUE(task_id)`. CANCELED требует enum-причину; DONE не имеет причины. DB trigger блокирует UPDATE/DELETE, а `FOR UPDATE` блокирует двойное завершение.

Сервис перед каждым чтением/изменением заново вызывает `auto_crm_leads._authority/_lead` для live session, роли и verified customer/vehicle link. Дедупликация конкурентного создания использует advisory transaction lock по lead/kind/due + уникальный индекс; статус/выполнение — row lock на задаче. HTTP POST требует CSRF; GET/POST `Cache-Control:no-store`. Нет публичного OpenAPI и исходящих сетевых действий.

```text
GET  /api/v1/dev/auto/organizations/{org}/locations/{loc}/crm/leads/{lead}/followups
POST /api/v1/dev/auto/organizations/{org}/locations/{loc}/crm/leads/{lead}/followups
  {"requestKey":"<UUID>","kind":"CHECK_DOCUMENTS","dueDay":"2030-01-10"}
POST /api/v1/dev/auto/organizations/{org}/locations/{loc}/crm/leads/{lead}/followups/{task}/finish
  {"requestKey":"<UUID>","action":"DONE","reasonCode":null}
POST /api/v1/dev/auto/organizations/{org}/locations/{loc}/crm/leads/{lead}/followups/{task}/finish
  {"requestKey":"<UUID>","action":"CANCELED","reasonCode":"NO_LONGER_NEEDED"}
```

GET возвращает `{"syntheticOnly":true,"items":[...]}`, не более 50 по сроку; POST возвращает `{"syntheticOnly":true,"replayed":false/true,"task":{...}}`, статус 201/200. `createdAt`/ `closedAt` — UTC instants, `dueDay` — локальная календарная дата филиала. Для ошибочной операции 409/422/403; feature gate 404.

## Приёмка и откат

`tests/integration/test_pg_auto_crm_followups_http.py`: HTTPS+PG17 полный create→retry→DONE, create→CANCELED с причиной, невалидный срок, второй исход, запрет изменения аудита, MANAGER/INTAKE, отзыв гранта и verified proof, BOLA, отсутствие PII, feature-off, конкурентный exact-key replay. `apps/web/e2e/auto-crm-followups.spec.ts`: совместный CRM экран, 390/1280, dark, create/DONE/CANCELED, 403 при смене филиала, malformed JSON fail-closed.

Миграция 0099 не изменяет таблиц 0098. Downgrade отказывается, если есть хоть одна задача: нельзя удалять append-only историю без изолированного backup/restore. Не реализованы расписание автоматических напоминаний, физические звонки, consent, отписка, маркетинговые правила, CRM revenue или production. Эпик #169 и #119 остаются OPEN.
