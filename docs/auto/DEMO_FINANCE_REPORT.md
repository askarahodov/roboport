# Управленческая сверка двух синтетических журналов (#170, #171)

> **Только current-dev, synthetic HTTPS loopback + PostgreSQL 17.** Это не выручка,
> доходы, прибыль, расчёт с клиентом, фискальный документ, бухгалтерский учёт,
> возврат настоящих денег или production finance. Legal/security gate #119 не пройден.

## Сценарий руководителя

В dev-кабинете откройте **«Тестовая финсверка (dev)»** по пути
`/staff/auto-demo-finance-report`. Укажите UUID **своей** организации и филиала,
локальные даты начала/конца (1–31 включительно), нажмите
**«Сформировать сверку»**. Доступ только через действующую серверную сессию
и живой `MANAGER` grant **именно этого филиала**.

Отчёт показывает два **несводимых** журнала и число запечатанных демонстрационных
предварительных пакетов:

### Журнал условных корректировок WorkOrder (без валюты)

Это `auto_work_order_financial_event`, запись только авторизованным
сценарием `post_work_order_financial`. Здесь **нет определённой валюты**
(`financeCurrency: null`), следовательно числовые значения
`chargeMinor/creditMinor/correctionNetMinor` — **условные единицы**
для тестирования инвариантов, **не RUB или денежный доход**.

- `chargeEvents` / `chargeMinor`: число `CHARGE` и сумма `amount_minor`.
- `creditEvents` / `creditMinor`: число `CREDIT` и сумма `amount_minor`.
- `correctionNetMinor = chargeMinor - creditMinor` **за локальный день регистрации события**.

Дневное сальдо может быть отрицательным, если корректировка относится к
начислению в другой день. Это **не** остаток по заказ-наряду и не `due`.
Повтор с тем же idempotency key не добавляет событие. Кредит сверх
доступного синтетического баланса запрещён доменным DB-guard.

### Журнал демонстрационных расчётов (условные копейки RUB)

Это **отдельный** `auto_order_demo_cash_event` после согласованной сметы,
завершённого исполнения и независимого QC PASS. Никакого движения денег,
интеграций банка, эквайринга, кассы, ККТ или 54-ФЗ нет.

- `demoPaymentEvents` / `demoPaymentMinor`: число и сумма `SIM_PAYMENT`.
- `demoRefundEvents` / `demoRefundMinor`: число и сумма `SIM_REFUND`.
- `demoNetMinor = demoPaymentMinor - demoRefundMinor` за локальный день **записи события**. Результат может быть отрицателен между днями.
- `sealedPreviewCount`: число неизменяемых `auto_order_demo_handover_preview`, созданных в этот день. Пакет **не акт выдачи, не чек и не оплата**.

Симулированный возврат не может превысить сумму ранее симулированных оплат
по конкретному WorkOrder. После `seal_handover_preview` новые имитации
запрещены. Без подтверждённого QC и одобренной сметы имитации не создаются.
Суммы **двух журналов никогда не складываются друг с другом** и не
претендуют на reconciliation реальной выручки.

### API, ограничения, безопасность

```text
GET /api/v1/dev/auto/organizations/{org_id}/locations/{loc_id}/demo-finance-report
    ?fromDay=2030-10-27&toDay=2030-10-27
```

Ответ `200` с `Cache-Control: no-store`:

```json
{
  "syntheticOnly": true,
  "legalGatePassed": false,
  "releaseAllowed": false,
  "financeCurrency": null,
  "demoCurrency": "RUB",
  "timeZone": "Europe/Berlin",
  "basis": "INDEPENDENT_LEDGER_EVENT_LOCAL_DAYS",
  "items": [{
    "localDay": "2030-10-27",
    "chargeEvents": 1, "chargeMinor": 4500,
    "creditEvents": 1, "creditMinor": 1200,
    "correctionNetMinor": 3300,
    "demoPaymentEvents": 2, "demoPaymentMinor": 3000,
    "demoRefundEvents": 1, "demoRefundMinor": 500,
    "demoNetMinor": 2500, "sealedPreviewCount": 1
  }]
}
```

Даты определяются в **IANA часовой зоне филиала** через полуинтервалы
`[D 00:00, (D+1) 00:00)`, включая 23/25-часовые DST сутки, независимо от
серверного UTC. Все строки фильтруются по исходному WorkOrder
`organization_id/location_id`; менеджер не получает order IDs, VIN,
actor/customer IDs, имя/телефон и текст `reason`.
Право проверяется заново на каждый запрос с блокировкой grant до конца транзакции.
Чужая организация, отсутствие MANAGER или отзыв роли → `403`;
некорректный UUID/дата/период больше 31 суток → `422`;
выключенный synthetic feature gate → `404`. Прежний отчёт UI
очищается при 403, смене филиала или ошибочном/недостоверном ответе.

**Важно:** daily totals не являются остатками за весь срок, денежным потоком
или анализом комиссий, налогов и прибыли. Здесь нет суммы invoice, VAT,
стоимости материалов или зарплаты. Доступ к реальным финансовым данным
откроется только после отдельного подтверждения #119 и соответствия #170/#171.

### Сквозная проверка и безопасный откат

`tests/integration/test_pg_auto_demo_finance_report_http.py`:
полный синтетический WorkOrder до QC PASS → CHARGE 4500 + CREDIT 1200
с idempotency replay и запретом перепогашения → SIM_PAYMENT 2500,
SIM_REFUND 500, SIM_PAYMENT 500 (проверка запрета превышения возврата) →
запечатывание предварительного пакета с idempotency replay →
сверка **обоих** независимых SQL журналов, нулей и ежедневных полей,
RBAC/BOLA и отзыва MANAGER, отсутствие PII, 422 и 404.

`apps/web/e2e/auto-demo-finance-report.spec.ts`: адаптивность 390/1280,
тёмная тема, заголовки двух журналов, состояние 403, очистка после смены
филиала, строгая проверка отрицательных/нестыкующихся сумм и попытки
подменить валюту начислений.

Нет новых миграций/таблиц и SQL-записей из отчёта. Откат — удалить
GET handler и страницу без воздействия на append-only журналы.
