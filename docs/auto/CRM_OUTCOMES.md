# CRM → технически закрытый ЗН → синтетическая касса (#444)

**Статус: current-dev, только закрытый HTTPS loopback, PG17 и тестовые записи.**
Это не выручка, не paid CRM conversion, не ККТ/эквайринг и не
юридический акт. `realMoney=false`, `revenueAvailable=false`,
`releaseAllowed=false`; gate [#119](https://github.com/askarahodov/roboport/issues/119)
не пройден. Интеграции, реальные ПДн, подписанные документы и production запрещены.

## Руководителю

1. Войдите с действующим MANAGER назначением своего филиала и правом
   `ESTIMATE READ/PRICE_SALES` и `HANDOVER READ/PRICE_SALES`.
   При deny на любой из этих capabilities вывод 403, даже если MANAGER
   сохранил другую роль.
2. На странице `/staff/auto-crm-outcomes` задайте UUID организации и
   филиала и период **создания CRM-лидов**, от 1 до 31 календарного дня.
   Границы считаются в TZ филиала, включая переходы DST. Кнопка
   «Проверить техническую конверсию» не изменяет данные.
3. Отчёт берёт только последнюю стадию BOOKED живого, независимо
   подтверждённого `auto_vehicle_customer_link`. До расчёта проверяются
   действующая организация, филиал, binding и отзыв связи. Отказ/новый
   лид/неподтверждённый или отозванный клиент не попадают в суммы.
4. `bookedLeads` — число подходящих лидов; `closedTechnical` —
   число **однозначно атрибутируемых** WorkOrder с событиями `POSTED`
   и `CLOSED_TECHNICAL`, действующим бронированием и разрешённой историей.
   Если к одной записи привязаны несколько BOOKED лидов или более одного
   WorkOrder, ни одному лиду **не приписывается финансовый результат**.
   Такие лиды могут остаться в числе записанных.
5. `simSettledMinor` = сумма независимых `PAYMENT` +
   `APPLY_ADVANCE` − `REFUND_PAYMENT` из `auto_treasury_event`
   **только закрытых технически** заказов своего филиала;
   `simRefundMinor` показывает `REFUND_PAYMENT` отдельно.
   `ADVANCE` без зачёта, `REFUND_ADVANCE`, legacy
   `SIM_PAYMENT`, суммы смет и незакрытые документы **не являются оплатой**
   и не участвуют. Это тестовый зачёт (копейки), а не выручка/маржа.
6. CSV скачивается свежим запросом с повторной проверкой живого
   MANAGER/документного ACL, а не из устаревшего состояния браузера.
   В CSV только фиксированные названия источников и числовые показатели;
   нет ID, ФИО, VIN, деталей заказа и формул Excel. При 403 интерфейс
   очищает старые данные.

## API, защита и критерии

- `GET /api/v1/dev/auto/organizations/{org}/locations/{loc}/crm/outcomes?fromDay=YYYY-MM-DD&toDay=YYYY-MM-DD`
- `GET .../crm/outcomes/export?fromDay=...&toDay=...` — UTF-8 CSV c BOM.
- Оба запроса: `Cache-Control: no-store`, synthetic-only HTTPS loopback;
  `403` при отсутствии/отзыве MANAGER, филиала, ценовой/исторической ACL;
  `422` при некорректном UUID, периоде или обнаруженной поломке
  целостности источника. Нет мутирующего endpoint или новой миграции.
- В ответе всегда `syntheticOnly=true`, `realMoney=false`,
  `releaseAllowed=false`, `revenueAvailable=false`.
  `simSettledMinor` и `simRefundMinor` передаются строками для
  безопасной обработки больших целочисленных сумм в браузере.
- Сценарий тестирования: создать verified link → CRM NEW → QUALIFIED →
  BOOKED на Appointment → связанный WorkOrder → technical POSTED →
  independent client ACK → CLOSED_TECHNICAL → отдельные CASH_SIM события.
  До закрытия сумма в CRM должна быть 0 даже при предоплате.
  Проверить отзыв proof/grant, чужой филиал, неверные даты, CSV.
- Проверка: `tests/integration/test_pg_auto_crm_outcomes_http.py`,
  `apps/web/e2e/auto-crm-outcomes.spec.ts`; job-ы CI `postgres`,
  `web`, `browser-e2e`, `api`, `windows-cmd`.

## Известные ограничения, откат

Никакой production/real-money конверсии, bank/receipt reconciliation,
реальных коммуникаций или исторических snapshots CRM-стадий. Для полной
[#444](https://github.com/askarahodov/roboport/issues/444) остаются
проверенный юридический денежный контур (#119/#170/#445), соглашения
и privacy/consent, real deal transitions, независимая приёмка и
проверка реальной истории. Нельзя объявлять исходный эпик завершённым.

Откат кода: вернуть маршруты CRM outcomes и UI, оставив существующие
таблицы/документы неизменными. Новых миграций и записываемых данных нет.
