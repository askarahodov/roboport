# Навигатор операций автосервиса: роль → задача → доказательство

> **Проверено по ролевой матрице и руководствам:** 09.11.2026, автомобильный `current-dev` — только закрытый synthetic HTTPS loopback.
>
> **Актуальность бренда:** «БоксРитм» / Auto Digital (#110/#121), актуальная dev-навигация синхронизирована с React и Docsify. Ребрендинг не означает разрешение на production или юридические операции. **Не production:** нет реальных ПДн, VIN, приёма денег, юридических подписей или выдачи автомобиля. Исторический robot HTTP не часть текущего runtime; архив SQL/OpenAPI хранится отдельно по #165. Gate #119 не пройден.

Выберите рабочую задачу в таблице и перейдите в подробную инструкцию роли. Таблица **не** предоставляет права доступа: сервер на каждом запросе проверяет сессию, организацию, филиал и конкретный объект. Для отрицательных сценариев используйте разные тестовые аккаунты, а не подмену роли.

## Для персонала СТО и клиента

| Задача | Роль и предварительные условия | Экран synthetic dev | Руководство и тест | Отказ / безопасное восстановление |
|---|---|---|---|---|
| Просмотреть назначения и отзывы | **ADMIN** своей организации; только чтение | `/staff/auto-admin` → «Показать журнал» | [Администратор](administrator.md) · [auto-staff-console.spec.ts](https://github.com/askarahodov/roboport/blob/main/apps/web/e2e/auto-staff-console.spec.ts) · [test_pg_auto_org_staff_http.py](https://github.com/askarahodov/roboport/blob/main/tests/integration/test_pg_auto_org_staff_http.py) | 403 чужая организация → проверить grant; 422 лимит → исправить значение |
| Привязать автомобиль к клиенту | **INTAKE** формирует pending; **CLIENT** предъявляет код; независимый **MANAGER** подтверждает | `/staff`, `/account/vehicles` | [Приёмщик](intake.md) · [Клиент](client.md) · [auto-staff-review.spec.ts](https://github.com/askarahodov/roboport/blob/main/apps/web/e2e/auto-staff-review.spec.ts) · [test_pg_auto_vehicle_claim_http.py](https://github.com/askarahodov/roboport/blob/main/tests/integration/test_pg_auto_vehicle_claim_http.py) | 403 нет полномочий, 429 лимит попыток → не подбирать код и не использовать чужой UUID |
| Создать запись на пост | **INTAKE** или **MANAGER**, своя организация/филиал; подтверждённая связь | `/staff/auto-calendar` | [Приёмщик](intake.md) · [auto-calendar.spec.ts](https://github.com/askarahodov/roboport/blob/main/apps/web/e2e/auto-calendar.spec.ts) · [test_pg_auto_appointment_http.py](https://github.com/askarahodov/roboport/blob/main/tests/integration/test_pg_auto_appointment_http.py) | 409 занят пост/состояние изменилось → перечитать календарь, не создавать дубликат |
| Самостоятельно записаться / перенести / отменить | **CLIENT**, собственный verified link и доступное окно до работ | `/account/auto-booking` | [Клиент](client.md) · [Контракт](../auto/CUSTOMER_BOOKING.md) · [customer-auto-booking.spec.ts](https://github.com/askarahodov/roboport/blob/main/apps/web/e2e/customer-auto-booking.spec.ts) · [test_pg_auto_customer_booking_http.py](https://github.com/askarahodov/roboport/blob/main/tests/integration/test_pg_auto_customer_booking_http.py) | 403 нет связи, 409 слот занят/операция устарела → обновить страницу; не считать dev-запись реальной |
| Назначить услуги и зафиксировать осмотр | **INTAKE**, открытый WorkOrder в своём филиале | `/staff/auto-order-services`, `/staff/auto-workshop-qc` | [Приёмщик](intake.md) · [auto-order-services.spec.ts](https://github.com/askarahodov/roboport/blob/main/apps/web/e2e/auto-order-services.spec.ts) · [auto-workshop-qc.spec.ts](https://github.com/askarahodov/roboport/blob/main/apps/web/e2e/auto-workshop-qc.spec.ts) | 409 смета уже опубликована/нарушена последовательность → не переписывать историю |
| Опубликовать версию сметы | **MANAGER** своего филиала; все услуги имеют цену в копейках | `/staff/auto-estimates` | [Руководитель](manager.md) · [auto-estimates.spec.ts](https://github.com/askarahodov/roboport/blob/main/apps/web/e2e/auto-estimates.spec.ts) · [test_pg_auto_work_order_estimate_http.py](https://github.com/askarahodov/roboport/blob/main/tests/integration/test_pg_auto_work_order_estimate_http.py) | 409 изменена версия/решение уже принято → перечитать immutable revision |
| Согласовать смету, просмотреть свою историю | **CLIENT** первоначального заказчика; только последняя смета | `/account/auto-orders` | [Клиент](client.md) · [customer-auto-orders.spec.ts](https://github.com/askarahodov/roboport/blob/main/apps/web/e2e/customer-auto-orders.spec.ts) · [test_pg_auto_customer_orders_http.py](https://github.com/askarahodov/roboport/blob/main/tests/integration/test_pg_auto_customer_orders_http.py) | 404 скрытый/чужой заказ, 409 устаревшая версия → вернуться в свой список |
| Выполнить назначенные работы | **MANAGER** запускает, **MECHANIC** фиксирует минуты; смета APPROVED | `/staff/auto-execution` | [Механик](mechanic.md) · [Руководитель](manager.md) · [auto-work-execution.spec.ts](https://github.com/askarahodov/roboport/blob/main/apps/web/e2e/auto-work-execution.spec.ts) · [test_pg_auto_work_order_execution_http.py](https://github.com/askarahodov/roboport/blob/main/tests/integration/test_pg_auto_work_order_execution_http.py) | 409 нет одобренной сметы, минуты/выдачи не завершены → исправить исходный этап |
| Получить/выдать детали и сверить остатки | **WAREHOUSE** своего филиала; резерв; отдельное подтверждение получателя | `/staff/warehouse-work-orders`, `/staff/auto-execution` | [Склад](warehouse.md) · [warehouse-work-orders.spec.ts](https://github.com/askarahodov/roboport/blob/main/apps/web/e2e/warehouse-work-orders.spec.ts) · [test_pg_auto_work_order_execution_http.py](https://github.com/askarahodov/roboport/blob/main/tests/integration/test_pg_auto_work_order_execution_http.py) | 403 чужой филиал; 409 конкурирующий резерв → перечитать ledger |
| Приход, пересортица, инвентаризация, возврат | **WAREHOUSE** создаёт; независимый **MANAGER** утверждает там, где требуется | `/staff/warehouse-receiving`, `/staff/warehouse-stocktake`, `/staff/warehouse-reclassification`, `/staff/warehouse-supplier-returns` | [Склад](warehouse.md) · [warehouse-receiving.spec.ts](https://github.com/askarahodov/roboport/blob/main/apps/web/e2e/warehouse-receiving.spec.ts) · [warehouse-stocktake.spec.ts](https://github.com/askarahodov/roboport/blob/main/apps/web/e2e/warehouse-stocktake.spec.ts) | 409 конфликт партии/ревизии → не обходить maker/checker; реальные документы запрещены |
| Провести независимый контроль качества | **MECHANIC**, не исполнитель проверяемых работ; после завершения | `/staff/auto-workshop-qc` | [Механик](mechanic.md) · [auto-workshop-qc.spec.ts](https://github.com/askarahodov/roboport/blob/main/apps/web/e2e/auto-workshop-qc.spec.ts) · [test_pg_auto_workshop_qc_http.py](https://github.com/askarahodov/roboport/blob/main/tests/integration/test_pg_auto_workshop_qc_http.py) | 403 self-QC, 409 работы ещё не завершены → пригласить другого проверяющего |
| Условно сверить заказ и сформировать техпакет | **MANAGER**, все gates исполнены; **WAREHOUSE** освобождает резерв | `/staff/auto-demo-close` | [Руководитель](manager.md) · [Склад](warehouse.md) · [auto-demo-close.spec.ts](https://github.com/askarahodov/roboport/blob/main/apps/web/e2e/auto-demo-close.spec.ts) · [test_pg_auto_demo_close_http.py](https://github.com/askarahodov/roboport/blob/main/tests/integration/test_pg_auto_demo_close_http.py) | 409 нет QC, остатки не закрыты → исправить блокер; `releaseAllowed: false` |
| Посмотреть технический пакет | **CLIENT** исходного заказчика, без юридической подписи | `/account/auto-demo-close` | [Клиент](client.md) · [auto-demo-close.spec.ts](https://github.com/askarahodov/roboport/blob/main/apps/web/e2e/auto-demo-close.spec.ts) | 404 чужой заказ → не перебирать идентификаторы; пакет не разрешает выдачу |

## Вход в кабинет и безопасные настройки (общие маршруты)

| Шаг | Рабочий маршрут | Подтверждённое поведение | Если нет доступа |
|---|---|---|---|
| Открыть стартовую страницу | `/` | Обзор автомобильных synthetic-dev сценариев; не публичное предложение услуг | Страница не предоставляет grant и не открывает закрытые данные |
| Войти в клиентский кабинет | `/account/login` | Тестовая сессия через защищённый backend, без хранения токена в localStorage | 401 — пройти авторизацию, не использовать чужую сессию |
| Открыть защищённый обзор | `/account` | Сервер проверяет `/api/v1/auth/me` перед отрисовкой; неавторизованный получает 401, отзыв прав — 403 | 403 — запросить законное назначение; повторная авторизация не восстанавливает отозванный grant |
| Проверить свой профиль/настройки | `/account/settings` | Страница внутри той же серверно защищённой сессии; не является разрешением обрабатывать реальные ПДн | 401/403 — не обходить проверку прямым URL |

Основные клиентские разделы `/account/vehicles`, `/account/auto-booking`, `/account/auto-orders` задокументированы ниже. Текущая навигация реализована в [CustomerAccount.tsx](https://github.com/askarahodov/roboport/blob/main/apps/web/src/pages/customer/CustomerAccount.tsx), вход — [CustomerLogin.tsx](https://github.com/askarahodov/roboport/blob/main/apps/web/src/pages/customer/CustomerLogin.tsx); проверка доступности: [Клиент](client.md), [Матрица](ACCESS_AND_TEST_MATRIX.md). Роуты `/account/*` и `/staff/*` — лишь контейнеры и не доказательство общих полномочий.

## Дополнительные рабочие экраны в автомобильном runtime

Указанные ниже маршруты присутствуют в [React-маршрутизаторе main.tsx](https://github.com/askarahodov/roboport/blob/main/apps/web/src/main.tsx). Это **не** подтверждение production-доступа: серверные права/проверки определяются соответствующими сценариями; роль в меню не даёт права на чужой филиал или заказ. Экран с неполным договорным/юридическим процессом остаётся только `current-dev`.

| Задача | Роль и граница доступа | Экран synthetic dev | Подробности | Ошибка / запрет |
|---|---|---|---|---|
| Зафиксировать диагностику по согласованным услугам | **MECHANIC** фиксирует находки; INTAKE/MANAGER читают результат с правами своей организации | `/staff/auto-diagnostics` | [Приёмщик](intake.md) · [Механик](mechanic.md) | 403 при чужом заказе или отсутствии роли; заключение не является юридическим актом |
| Приёмочный акт и предпросмотр доказательств | Только уполномоченный сотрудник своей организации; **CLIENT** читает только документ первоначального заказчика | `/staff/auto-intake-act`, `/account/auto-intake-act` | [Приёмщик](intake.md) · [Клиент](client.md) | 403 после отзыва proof; фотографии и предпросмотр **не** цифровая подпись или выдача (#439/#119) |
| Посмотреть отдельную страницу своей сметы | **CLIENT** первоначального заказчика, проверка точной версии и сессии | `/account/auto-estimates` | [Клиент](client.md) · [Сметы](../auto/ESTIMATES.md) | 404 чужая история, 409 устаревшая версия; согласие dev не юридическое |
| Отчёт загрузки постов | **MANAGER** конкретного филиала, синтетические календарные дни | `/staff/auto-bay-occupancy` | [Руководитель](manager.md) · [Методика](../auto/REPORTING.md) | 403 чужой филиал, 422 неверный период; процент по календарным, не оплачиваемым часам |
| Два условных финансовых журнала | **MANAGER** своего филиала; нет права признания реальной выручки | `/staff/auto-demo-finance-report` | [Руководитель](manager.md) · [Ограничения](../auto/DEMO_FINANCE_REPORT.md) | 403 не свой филиал; `SIM_PAYMENT/SIM_REFUND` **не** деньги или чеки |
| Создать обращение CRM и связать с записью | INTAKE или MANAGER только при действующем verified customer-link и собственном филиале | `/staff/auto-crm-leads` | [Приёмщик](intake.md) · [CRM](../auto/CRM.md) | 403 после revoke, 409 несовместимая запись; переход BOOKED не означает оплату |
| Посмотреть воронку обращений CRM | **MANAGER** со scoped правами на филиал | `/staff/auto-crm-funnel` | [Руководитель](manager.md) · [CRM](../auto/CRM.md) | Никакой сквозной достоверной финансовой конверсии без фактической оплаты (#444) |
| Сверить CRM с техническим закрытием и SIM расчётами | **MANAGER** своего филиала с разрешением читать сметы/пакеты выдачи и видеть продажные цены; только синтетика | `/staff/auto-crm-outcomes` | [Руководитель](manager.md) · [Алгоритм/rollback](../auto/CRM_OUTCOMES.md) · [PG17](https://github.com/askarahodov/roboport/blob/main/tests/integration/test_pg_auto_crm_outcomes_http.py) | 403 отказ price/role ACL, 422 период; SIM зачёт **не** фактическая выручка, #444/#119 OPEN |
| Анализ количества выдач и возвратов запчастей ABC/XYZ | **MANAGER** филиала, 3–12 завершённых месяцев; WAREHOUSE не получает новое право | `/staff/auto-stock-abc-xyz` | [Руководитель](manager.md) · [Склад](warehouse.md) · [Методика](../auto/REPORTING.md) | 403 чужой филиал; это количество движения SKU, а не прибыль или оценка денег |
| Серийные перемещения запасов | **WAREHOUSE** своей организации/филиала; ledger и границы партии | `/staff/warehouse-serial-movements` | [Склад](warehouse.md) · [Матрица](ACCESS_AND_TEST_MATRIX.md) | 409 дублирующее или конфликтное движение; нельзя отгружать реальные товары |

## Отсутствующие в продукте операции — не искать кнопку

| Роль | Статус | Что нельзя выполнять | Причина и будущая инструкция |
|---|---|---|---|
| Бухгалтер / кассир | **planned** | Реальная оплата, аванс, возврат денег, фискальный чек, ДДС | [Бухгалтер](finance.md) · #170/#119; `SIM_PAYMENT` и `SIM_REFUND` **не** деньги |
| Клиент | **planned для production** | Публичная запись с реальными данными, юридически значимые согласия и получение автомобиля | [Клиент](client.md) · #117/#119; отдельный synthetic booking **current-dev** |
| Администратор | **planned для production** | Боевой bootstrap организации и полноценное управление полномочиями из UI | [Администратор](administrator.md) · #166/#119 |
| Мобильный клиент | **planned** | Нативный iOS/Android вход, push, offline, mobile-платежи | [Мобильное приложение](mobile.md) · #118 |
| Персонал СТО | **planned для production** | Подписанный акт и фактическая выдача автомобиля | [Приёмщик](intake.md) · #439/#441/#119 |

## Как трактовать ошибки и прерванные операции

**401** — повторно войти тестовой учётной записью. **403** — проверить действующую роль, организацию, филиал и клиентскую связь, **не** подставлять чужой ID. **404** — неизвестный или скрытый объект: его нельзя открывать перебором UUID. **409** — обновить состояние, номер последней сметы, резерв или календарь; не редактировать неизменяемые документы. **422** — исправить формат входа. **429** — дождаться лимита, не перебрать коды подтверждения.

Если связь оборвалась после POST, **не создавайте новый запрос со свежим ключом** для оплаты, резерва, записи или согласия. Перечитайте журнал/состояние или используйте тот же idempotency key, **только если конкретный endpoint разрешает такой replay в текущем состоянии**. После отмены/переноса старая бронь не становится актуальной от успешного прежнего ключа: возможен 409.

## Источники истины и доказательства

- [Матрица доступов и E2E](ACCESS_AND_TEST_MATRIX.md) — проверяемые роли, имена UI-файлов и реальные сценарии; [реестр операций](../auto/OPERATIONS_EVIDENCE.md) — негативные PG17, BOLA, ACL, ревокация, конкуренция.
- [Быстрый старт](QUICK_START.md) — полный заказ-наряд от записи до технического пакета; [FAQ](FAQ.md) — диагностика отказов; [снимки и ручная QA](SCREENSHOT_EVIDENCE.md) — 390/1280, light/dark.
- Автоматические тесты в GitHub Actions **не** заменяют независимое review/ручную QA #76 и security/legal/ops gate #119. Доступные dev-операции не означают готовность коммерческой СТО.

**Статус приёмки:** эта карта — current-dev документация, не подтверждение полного выполнения #173. После изменения UI или серверной роли исправляйте в том же PR эту карту, ролевое руководство, матрицу и тесты.

## Заказ поставщику по дефициту (current-dev #443)

[Кладовщик](warehouse.md) → `/staff/warehouse-supplier-orders`: синтетический WorkOrder/SKU, сравнение двух offline предложений, частичные документы и отмена оставшейся поставки. Физическая приёмка/стоимость — по действующему `/staff/warehouse-receiving`, отдельно подтверждает MANAGER. Исходники и негативные доказательства: [SQL/ACL/PG17](../../tests/integration/test_pg_auto_supplier_purchase.py), [HTTP](../../tests/integration/test_pg_auto_supplier_purchase_http.py), [Browser E2E](../../apps/web/e2e/warehouse-supplier-orders.spec.ts), [полная процедура](../auto/SUPPLIER_PURCHASES.md). 403/409/422 не обходить подстановкой UUID; настоящий supplier/оплата `planned` до #119.

## Проверка компонентов и визуальный аудит

- **QA/дизайнер, только synthetic dev:** `/dev/ui-gallery` — статическая проверочная галерея кнопок, форм, состояний 403/409, пустого экрана и клавиатурного диалога. Она не открывает клиентские данные и не наделяет правами. Визуальные PNG/manifest по #92 генерирует [visual-audit.spec.ts](https://github.com/askarahodov/roboport/blob/main/apps/web/e2e/visual-audit.spec.ts) для независимого sign-off; это не одобренный screenshot baseline. [Процедура проверки](SCREENSHOT_EVIDENCE.md).


## Гранулярные права документов и закупочных цен (current-dev, #446)

**ADMIN** в `/staff/auto-admin` → «Показать ограничения документов» →
«Новая версия правила» → «Сохранить правило». Отдельная проверка:
введите UUID действующего сотрудника/филиала и нажмите
«Проверить эффективные права». Вывод отражает роль/политику,
**не** разрешение доступа к чужому заказу. Запрет можно снять
только новым событием ALLOW в том же scope; личное ALLOW не
перепрыгнет шаблонный DENY. Роль сотрудников нельзя назначить
самим ACL-правилом. Для **INTAKE** ограничения действуют на акт
приёмки и приватные фото, для **MANAGER** — на сметы/скидки/
технический пакет, для **WAREHOUSE** — на supplier order,
историю и закупочные цены. `403` → проверить живой grant и
филиал; `409` → перечитать аудит/версию; `422` → исправить
тело; неизвестный POST → повторить **исходный** ключ.
[Инструкция ADMIN](administrator.md) ·
[контракт/матрица ACL](../auto/DOCUMENT_ACCESS.md) ·
[PG17](https://github.com/askarahodov/roboport/blob/main/tests/integration/test_pg_auto_document_access_http.py) ·
[Playwright](https://github.com/askarahodov/roboport/blob/main/apps/web/e2e/auto-document-access.spec.ts).


## Кассы и авансы (synthetic dev, #445)

Финансовое рабочее место: `/staff/auto-treasury` —
MANAGER создаёт счёт и сверяет отчёты; CASHIER с отдельным
организационным грантом добавляет аванс/частичную оплату,
зачёт, возврат и закрывает день. Маршрут dev, без денег, ККТ,
банковских callback или юридически действительных документов.
[Ролевой walkthrough](finance.md) · [полная модель](../auto/FINANCE.md).

## Публичная запись #442 DEV-only

Анонимный посетитель: `/book-dev` (услуга/время/арифметическая проверка/условия). Исходный CLIENT: `/account/auto-public-booking` (verified claim, receipt status, marketing withdrawal). MANAGER(+WAREHOUSE): `/staff/auto-public-booking` (публикация PART/CONSUMABLE). INTAKE(+WAREHOUSE): там же (appointment, WorkOrder, stock reserve, CRM lineage). Не открывать реальные данные/уведомления до #119. [Runbook](../auto/PUBLIC_BOOKING.md).


## Справочник SKU и прайс-категорий (current-dev #447)

**WAREHOUSE+MANAGER с двумя живыми grants** → `/staff/warehouse-catalog`: создание immutable SKU-profile, версии тестовых закупочных/продажных цен, ручной offline-аналог, **логическое** объединение только при безопасных нулевых остатках, XLSX dry-run/preview → atomic commit и export. Каждая мутация проходит CSRF+ACL+tenant/branch+SQL guard, после 409 нужна сверка версий, после 503 прежний idempotency key. Без real provider/платежей/OEM. [Пошаговая инструкция](warehouse.md) · [SQL/API/тесты](../auto/SKU_CATALOG.md).

## Проверяемая выработка механиков (current-dev, #448)

**MANAGER** своей организации и филиала открывает
`/staff/auto-mechanic-productivity` → вводит UUID организации/филиала и
период 1–31 день → нажимает **«Рассчитать выработку»** → получает
фактические минуты завершённых технических заказ-нарядов с QC PASS,
детализацию по UUID механиков и нарядов и CSV. В случае 403 проверить
MANAGER/HANDOVER:READ (сами ссылки не дают прав); при 409 сократить
период (более 500 записей), при 422 исправить даты. Отозванные
механики/подтверждения и старые документы за cutoff не отображаются.
**Не** зарплата, прибыль, реальная касса или бухгалтерский документ.
[Роль руководителя](manager.md) ·
[Методика](../auto/REPORTING.md) ·
[PG17/HTTPS тест](https://github.com/askarahodov/roboport/blob/main/tests/integration/test_pg_auto_mechanic_productivity_http.py) ·
[Playwright](https://github.com/askarahodov/roboport/blob/main/apps/web/e2e/auto-mechanic-productivity.spec.ts).


## Синтетическая кассовая книга и контроль закрытых дней (#445)

**Роли: MANAGER / CASHIER**, но не клиент, приёмщик без роли кассира
или чужой филиал. Адрес `/staff/auto-treasury-cashbook`; укажите
UUID организации, филиала, условного счёта, даты 1–31 день →
**«Сверить кассовую книгу»**. Раскройте «Событий: N» для просмотра
первичных immutable UUID, проверьте приход/возврат и «Закрыто»,
скачайте CSV только с повторной авторизацией. `403` при revoked
сессии/роли/финансовой политике, `409` если больше 1000 событий
или нарушена сверка, `422` при неверных датах. Нет имён клиентов,
VIN, произвольных заметок или юридических финансовых записей.
[Бухгалтер/кассир](finance.md) · [Финансовый контур](../auto/FINANCE.md) ·
[PG17 HTTPS](https://github.com/askarahodov/roboport/blob/main/tests/integration/test_pg_auto_treasury_cashbook_http.py) ·
[Playwright](https://github.com/askarahodov/roboport/blob/main/apps/web/e2e/auto-treasury-cashbook.spec.ts).
