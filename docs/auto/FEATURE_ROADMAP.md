# Дорожная карта полного перехода на автомобильную систему

**Обновлена:** 28.10.2026 · Источник правды для dependency order: [#107](https://github.com/askarahodov/roboport/issues/107), [матрица](FUNCTIONAL_MATRIX.md), [продукт](PRODUCT.md). AutoDealer — функциональный ориентир, не источник дизайна или нелицензированных данных.

## Решение: не поддерживать «сервис для роботов + вкладка автомобилей»

**Конечная цель:** один продукт для предприятий автосервиса. В целевых пользовательских URL, UI, новой документации, опубликованном OpenAPI, доменной модели и рабочих потоках **нет роботов**. Исключение — строго ограниченный архив/миграционный код до [#165](https://github.com/askarahodov/roboport/issues/165). Нельзя автоматически переносить `service_ticket` в автомобильный `WorkOrder` или давать доступ к старой истории по VIN. Никаких destructive migrations, пока не готовы reconciliation, backup/restore и authority/LEGAL sign-off.

## Зависимости (основной критический путь)

| Фаза | Исход / что сдаём | Входные блокеры | Канонические задачи | Доказательство закрытия |
|---|---|---|---|---|
| P0. Требования | Полная карта бизнес-функций и сценариев 50+; решения о приоритетах | Нет | #174, #108, #107 | Матрица planned/current/legacy, Given/When/Then, проверенные источники и gaps |
| P0. Архитектура | Автомобильный модульный монолит, организация, права и cutover ADR | P0 discovery | #109, #166, #165 | Tenant-bound API/ACL, модель миграции/аудита и rollback |
| P0. Данные | Организация → филиалы → клиенты → авто/владение, real auth/consent | #109, #166 | #112, #130, #131 | Два владельца/две компании, права и отзыв, никакой старой истории по VIN |
| P1. Scheduling | Запись на пост, загрузка, перенос/отмена, self-service | #112, #166 | #113 | E2E двойной брони, 390/1280, разные филиалы |
| P1. WorkOrder | Приёмка, осмотр, диагностика, norm-hours, согласованная смета, QC, выдача | #113, #112 | #114, #167 | Сквозной подписанный наряд, версия сметы, безопасность и отрицательные переходы |
| P1. Parts | Авто SKU, локальный справочник/адаптер, резерв, выдача, приход, возврат | #114, #111 | #115, #168 | PG17 ledger, отсутствие отрицательных остатков и конкурентного двойного резерва |
| P1. Finance minimum | Документы, базовый баланс заказ-наряда, оплата/возврат в тестовом контуре | #114, #168 | #116, #170 | Double webhook/refund, reconcile, legal placeholders |
| P1. Customer web | Машины, запись, наряды, подтверждённая смета, безопасная история и документы | #112–#114, #130 | #117, #131, #124 | A/B isolation, openapi type generation, UI E2E |
| P1. Legacy exit | Полная замена robot маршрутов и бизнес-терминологии, архив/миграция | Все P1 бизнес-срезы | #165, #172, #110 | Отдельный decommission-gate, reverse migration drill, zero robot UI/API |
| P2. Commercial | CRM/воронка, закупки, несколько складов, деньги, отчёты, импорт | P1 data + WorkOrder | #169, #115, #170, #171, #172 | Сверка первички, отчёты, интеграционные контракты и ссылки на документы |
| P2/P3. Integrations | OEM/аналоги и нормочасы с лицензиями; телефония, уведомления, ККТ/СБП/ЭДО | Legal + provider access | #111, #169, #170, #119 | Sandbox/contract/failure tests, договоры и compliance |
| P3. Mobile | iOS/Android поверх версионированного API, безопасный mobile auth | #117 и auth ADR | #118 | E2E авторизации, отзыв токена, parity с web |
| P0 на всех фазах | Code/QA/docs/release gates | Каждый PR | #119, #121, #123, #124, #173, #92 | PR+main 5/5 CI, docs links, security, browser accessibility, restore |

**Зависимости по фазам**, не означает, что продвинутый CRM/финансовый учёт можно игнорировать: полный функциональный охват остаётся целью, отдельные интеграции требуют договоров и могут быть блокированы. Задача уровня эпика не закрывается после первого технического прототипа — для каждого вертикального среза создавать небольшие Issues с исполнимым DoD.

## Обязательные отдельные Issues

- [#165](https://github.com/askarahodov/roboport/issues/165): инвентаризация robot UI/API/DDL/fixtures → миграция → cutover → архив → decommission.
- [#166](https://github.com/askarahodov/roboport/issues/166): Organization/Tenant, ServiceLocation, bootstrap ADMIN, post, роли и филиалы.
- [#167](https://github.com/askarahodov/roboport/issues/167): осмотр/дефектовка/нормочасы/механик/QC.
- [#168](https://github.com/askarahodov/roboport/issues/168): операции склада, резервы, возвраты и инвентаризация.
- [#169](https://github.com/askarahodov/roboport/issues/169): CRM/воронка/коммуникации/повторный визит.
- [#170](https://github.com/askarahodov/roboport/issues/170): касса/авансы/возвраты/деньги/документы.
- [#171](https://github.com/askarahodov/roboport/issues/171): управленческие KPI, загрузка постов, маржа, зарплаты и ABC/XYZ.
- [#172](https://github.com/askarahodov/roboport/issues/172): dry-run CSV/XLSX импорт с idempotency/provenance и безопасными owner ACL.
- [#173](https://github.com/askarahodov/roboport/issues/173): общая русская база знаний для всех автомобильных ролей, без robot разделов в навигации.

## Что реально существует сегодня

**Действует только на синтетике (current-dev):** авто-таблицы, защищённые pending/verified links, dev endpoint, часть клиентского экрана и закрытый сценарий staff/proof. Дополнительный branch/MANAGER pilot не является production authority. **Legacy:** компоненты для робототехники (тикеты/сметы/запасы/веб-страницы), требующие перепроектирования контекста. **Planned:** все главные сквозные сценарии автосервиса, кроме изолированных auto-dev прототипов.

Каждая задача должна явно указывать `current-dev` ≠ `production`. Никогда не закрывать автосервисный WorkOrder как «готово», если тестировался только робототехнический `service_ticket`.

## Правило Definition of Done и закрытия

1. **Сценарий:** владелец, роли, разрешения, входные/выходные состояния, отказы, Given/When/Then.
2. **Сервер:** реальный endpoint + OpenAPI, доменная модель (не alias legacy), PostgreSQL миграции/constraints и транзакционные guards.
3. **Безопасность:** BOLA/IDOR, A/B company isolation, version/consent, sessions, revoke/race/idempotency, отсутствие cross-domain role escalation.
4. **Интерфейс:** рабочий сценарий без placeholder в web, корректная mobile 390/1280, keyboard, light/dark, ошибки/пустые состояния.
5. **Доказательства:** все обязательные PG/API/Web/Browser Linux/Windows CMD jobs GREEN на PR head и post-merge main; независимое ревью без нерешённых замечаний; негативные тесты не пропускать.
6. **Русская база знаний:** текущие действия/кнопки/ошибки по роли + скриншоты с synthetic данными в том же PR. Старый robot текст — только явно закрытый legacy архив.
7. **Релиз:** production gate #119 требует лицензий, договоров, security/privacy, backup/restore, мониторинга и миграции; до этого не утверждать готовность к боевой эксплуатации.

## Контроль cutover (это отдельный обязательный критерий продукта)

- Инвентаризованы все robot URLs/labels/entities/DDL/fixtures/contract routes/permissions/движения; для каждого назначено действие и ответственный.
- Нет тихого наследования robot `ticket.manage` при выдаче auto MANAGER, старые исторические файлы доступны только их законному владельцу/архиву.
- Новый авто UI и документация доступны из корня; старые маршруты выведены из текущей навигации и отключены в целевом режиме.
- Экспорт/восстановление выполнены на одноразовой копии; бухгалтерские и юридические сроки хранения учтены; удаление только после отдельно одобренного решения.
- В финальном CI нет «ложно зелёных» тестов, основанных на старых robot формах вместо автомобильных.


## Актуализация #168: отдельная себестоимость прихода (29.10.2026)

В закрытом синтетическом контуре PR по #168 последовательно реализуют отдельный приходный документ, независимую приёмку, экраны и учёт стоимости закупки и суммы входящего НДС (minor units) **вне физического stock ledger**. Реальные invoice/tax rules, восстановление стоимости после возврата/перемещения, оплата поставщику, серийность, производственный RBAC и легальные интеграции остаются planned. Основной #168 нельзя закрывать на основании отдельных current-dev PR; security/privacy/licensing gate #119 и закупочные контракты #115/#111 нужны отдельно.


## Склад, checkpoint 29.10.2026 (#168)

Пересортица имеет current-dev синтетический транзакционный maker/checker flow с лотами/складским ledger и экраном. Исходная #168 по-прежнему заблокирована незавершёнными production tenant DB role, серийным движением, коммерческими закупками и зависимостями #111/#114/#115/#119/#166. Не менять статус исходного эпика только на основании 5/5 CI частичной реализации.


## Серийные номера и движения (current-dev, #168)

Миграция `0071_auto_serial_movement` и staff UI покрывают трассируемую единицу от лота в заказ-наряд, назад в лот и между филиалами. Каждое движение участвует в одном физическом складском transaction; его код проверяется PG17/HTTP/Browser E2E. Production сценарий блокируют ранее отмеченные зависимости #111/#114/#115/#119/#166, правила обязательной серийной регистрации, поставщицкие документы и роли БД; не считать полный склад готовым.


## Финансовое происхождение возвратов поставщику (0072, current-dev)

Есть синтетический двухэтапный документ: складской акт возврата от WAREHOUSE и независимое утверждение MANAGER с физическим списанием и атрибуцией закупочного нетто и ранее записанного входного НДС исходного приходного документа. Это **не кредит-нота** и **не перерасчёт налогов**. По всей #168 остаются production least-privilege tenant DB ACL, реальная закупочная цепочка и юридическая приёмка #111/#114/#115/#119/#166; статус issue сохранять OPEN до полного закрытия критериев.


## Клиентский заказ-наряд и собственная история (current-dev; #117/#124)

В закрытом синтетическом dev `/account/auto-orders` клиент видит **собственные** автомобильные WorkOrders с подтверждённой связью оригинального заказчика, актуальным бронированием, историей смет, техническим согласием, работами, QC и immutable preview. Добавлены keyset paging по session-bound HMAC cursor, read-side revocation на каждой странице, исключение передачи истории новому владельцу той же машины и мобильный/desktop E2E. Полный контракт: [CUSTOMER_HISTORY.md](CUSTOMER_HISTORY.md). Это не production клиентский кабинет: самостоятельная запись, реальные документы/подписи, платежи, mobile auth и legal/security gate #119 остаются обязательны; #117 и #124 до полной области не закрывать.


## Пользовательская база знаний как единый release gate (03.11.2026)

Код клиентской истории `/account/auto-orders` дополнен
[полным быстрым стартом ролей](../guides/QUICK_START.md),
[матрицей экранов/API/тестов](../guides/ACCESS_AND_TEST_MATRIX.md),
[FAQ 401/403/409/429](../guides/FAQ.md),
[планом воспроизводимых скриншотов 390/1280](../guides/SCREENSHOT_EVIDENCE.md)
и [архивом robot-документации](LEGACY_ARCHIVE.md).
Автоматический Docsify link/search/keyboard/viewport smoke нельзя приравнивать
к завершённому manual QA, юридическому подписанию или decommission (#119/#165/#173).


## Календарь: различение ошибок запроса и прав доступа (current-dev, #113)

Закрытый synthetic endpoint `GET /api/v1/dev/auto/organizations/{org}/calendar` возвращает `422 INVALID_INPUT` для неверного диапазона (включая интервал более 7 суток, обратные или timezone-naive даты) и неполного курсора пагинации. Неверные UUID/даты также получают `422`. При отсутствии действующей роли `INTAKE`, при чужом филиале или организации возвращается `403 FORBIDDEN` без раскрытия данных. Нормальная страница не более 100 записей, результат `Cache-Control: no-store`. Контракт проверяется `tests/integration/test_pg_auto_calendar_http.py` на PG17. Это **не** разрешает production, не реализует уведомления, клиентскую self-service запись или юридические гейты #119; issue #113 остаётся открытой.


## Актуальность idempotency receipt для создания и переноса (current-dev, #113)

`POST /api/v1/dev/auto/organizations/{org}/appointments/{appointment}/reschedule` с тем же `Idempotency-Key` и неизменным payload возвращает исходный `bookingId` только пока эта бронь **актуальна и активна**. Если после удачного переноса запись отменена или повторно перенесена, старый receipt не считается подтверждением действующей брони: повтор возвращает `409 IDEMPOTENCY_KEY_REUSED` без новых движений/аудита. При повторе сначала заново проверяются сессия, полномочия филиала и подтверждённая связь автомобиля с клиентом. Покрытие: `tests/integration/test_pg_auto_reschedule_http.py` (успешный replay → отмена → 409). Аналогично `POST /api/v1/dev/auto/organizations/{org}/appointments` с прежним `Idempotency-Key` повторяет исходные `appointmentId`, `bookingId`, `workOrderId` только пока **исходная бронь является текущей и активной**. После переноса или отмены старый receipt больше не возвращает ложный успех: `409 IDEMPOTENCY_KEY_REUSED`. Неизменяемость Appointment/WorkOrder и число записей в истории проверяет `tests/integration/test_pg_auto_appointment_service.py`; HTTP 201 → cancel → 409 подтверждает `tests/integration/test_pg_auto_appointment_http.py`. В обоих случаях актуальные полномочия и verified link проверяются до чтения receipt. Всё остаётся внутри synthetic loopback; #113 и production gate #119 открыты.


## Транзакционные уведомления записи (current-dev, #113)

От `Appointment.CREATED`, `RESCHEDULED` и настоящей отмены создаются атомарные, не содержащие PII сообщения в PostgreSQL outbox. Worker осуществляет только `DEV_SINK` с ограниченными повторами и dead-letter; ни настоящих SMS/email, ни согласия пользователя, ни timezone филиала в этом прототипе нет. Контракт, критерии отказов/повторов, миграция и PG17-доказательства: [APPOINTMENT_NOTIFICATIONS.md](APPOINTMENT_NOTIFICATIONS.md). #113 остаётся OPEN до полного календаря/self-service/notifications и гейтов #119.


## Локальные сутки календаря филиала и переходы DST (current-dev, #113)

Миграция `0090_auto_location_timezone` добавляет `auto_service_location.timezone_name`: `UTC` для существующих тестовых филиалов, IANA зона для нового филиала на disposable стенде. Изменить зону разрешено **только до первой брони/смены**; изменение исторических календарных суток после этого запрещено БД. `GET .../calendar?locationId=...&localDay=YYYY-MM-DD` требует живую роль `INTAKE`, определяет полуоткрытые локальные сутки в зоне филиала (23/24/25 часов при переходе DST), возвращает сохранённые UTC instants и `timeZone`. Старый режим `startsAt+endsAt` для клиентов сохранён, одновременное использование или неверная дата дают `422`; чужой филиал или отсутствие прав — `403`. В `/staff/auto-calendar` выбранный день трактуется как местные сутки, время записей показывается в зоне филиала, и кандидаты слотов запрашиваются за те же **локальные сутки**, включая 23/25 часов DST (до 49 возможных стартов, каждые 30 минут, длительность 60 минут). Тесты PG17/DST и Playwright: `test_pg_auto_calendar_local_day.py`, `auto-calendar.spec.ts`. До production остаются реальные часы работы филиала и календарь смен, self-service, законное consent и #119. Не закрывать #113 на основании этой функции.


## Клиентская самостоятельная запись, перенос и отмена (current-dev, #113)

Для действующей роли `CLIENT` добавлен закрытый `/account/auto-booking` и сервер `auto_customer_booking_http.py`, использующий ту же атомарную запись Appointment→WorkOrder, текущий IANA день филиала, tenant-бронирование/конфликты и неизменяемый outbox, что и staff. Доступ проверяется по **исходной** verified-связи клиент–автомобиль и активному `account_customer_link`, а не по VIN или идентификатору заказа; права приёмщика не наследуются. При начале оформления работ клиент не может менять или отменять запись. Тесты: `test_pg_auto_customer_booking_http.py` и `customer-auto-booking.spec.ts`. Подробные шаги и ограничения: [CUSTOMER_BOOKING.md](CUSTOMER_BOOKING.md). Production consent, интеграции оплаты/уведомлений, service/materials atomic integration и release gate #119 по-прежнему planned; не закрывать #113 по одной dev-реализации.


## Атомарная запись с выбором услуг каталога (current-dev, #113)

Единый `create_appointment` для INTAKE и CLIENT принимает необязательные `serviceIds` (не более 8 уникальных UUID). Сервер сверяет принадлежность каталогу филиала и суммарную длительность с интервалом. Если нет прав (`403`), услуги чужие/несуществующие (`403`), длительность не помещается (`422 SERVICES_EXCEED_SLOT`) или пост занят (`409`) — откатывается **весь** Appointment→WorkOrder→service selection→outbox. Ключ идемпотентности связывает точный нормализованный состав работ с записью, предотвращая повторное создание с изменённым payload. Клиентский `/account/auto-booking` и staff `/staff/auto-calendar` имеют ограниченный выбор работ; staff каталог защищён отдельной живой ролью `INTAKE`, без наследования `MANAGER`; при заполненном WorkOrder самостоятельный перенос/отмена клиентом по-прежнему запрещены. [Пользовательская инструкция и API](CUSTOMER_BOOKING.md). Тесты `test_pg_auto_booking_services_atomic.py` и `customer-auto-booking.spec.ts`. Материалы/товары и цены, синтетический reserve ledger, согласие на смету, production authentication/privacy/legal #119 не входят в этот срез; #113 до завершения всей области не закрывать.


## Подбор слотов по длительности работ (current-dev, #113)

В закрытых API клиента (`GET .../customer/booking/slots`) и сотрудника (`GET .../organizations/{org}/slot-candidates`) повторяемые query-параметры `serviceId` выбирают не более 8 уникальных услуг своей организации/филиала. PostgreSQL вычисляет полную длительность по каталогу, а не по данным клиента; без выбора сохраняются одночасовые кандидаты. Начало слота кратно 30 минутам, окончание не выходит за границы UTC/локальных суток (23–25 часов при IANA DST), а SQL отсекает конфликты поста и занятость/смену сотрудника при назначении. Данные чужого каталога отклоняются (`403`), неверные UUID/дубли/более восьми значений — `422`; `guaranteed:false`, повторные проверки при создании обязательны. Оба UI очищают кандидаты при изменении выбранных услуг. PG17/HTTP тесты: `tests/integration/test_pg_auto_service_duration_slots.py`; browser E2E: `apps/web/e2e/customer-auto-booking.spec.ts`, `apps/web/e2e/auto-calendar.spec.ts`. Склад/материалы/стоимость, delivery/consent и production gate #119 остаются блокерами; #113 не закрывать.


## Безопасный lifecycle записи и складского резерва (current-dev, #113)

Миграция `0091_auto_booking_stock_guard` защищает от отвязанных запасов: отмена действующей брони с открытым `RESERVE` или непогашенной выдачей `ISSUE - RETURN` блокируется PostgreSQL даже при прямом UPDATE; STAFF получает `409 STOCK_RECONCILIATION_REQUIRED`, CLIENT — `403` и `canManage:false`. После полного `RELEASE` и/или `RETURN` без открытых складских обязательств отмена снова допустима. Перенос сохраняет WorkOrder и складские события; функции `record_auto_work_order_stock` и `record_auto_work_order_return` теперь проверяют последнюю актуальную бронь из immutable истории переносов. Regression tests `test_pg_auto_booking_stock_guard.py` и существующие `test_pg_auto_work_order_stock.py`. Остался внешний/полный workflow покупки, резерва и возврата материалов, цены, consent/privacy и release gate #119; **#113 остаётся открытой**.


Парные warehouse lot-функции `record_auto_stock_lot_work_order_issue` и `record_auto_stock_lot_work_order_return` также сверяют текущую бронь после переноса (не первоначальную), с PG17 полным циклом `test_pg_auto_booking_lot_move_reconcile.py`. Не допускается двойное списание партий или отмена до закрытия `ISSUE-RETURN` и `RESERVE-ISSUE-RELEASE`.


## Первичная запись с товарами и материалами склада (current-dev, #113)

Миграция `0092_auto_booking_materials` связывает неизменяемый выбор `PART`/ `CONSUMABLE` с настоящим складским `RESERVE` по каждому SKU. Работник должен обладать **живыми INTAKE + WAREHOUSE** на одном филиале: `GET .../booking-stock` показывает до 100 собственных синтетических SKU с `on_hand-reserved`, `POST .../appointments` принимает до восьми `materialItems`, проверяет scope/количество, Idempotency-Key и в одной PG17 транзакции сохраняет Appointment→WorkOrder→work services→stock RESERVE→classification snapshot→notification outbox. Нехватка доступного количества даёт `409 STOCK_UNAVAILABLE`; чужая позиция — `403`; старый ключ с другим составом — `409 IDEMPOTENCY_KEY_REUSED`; клиентский endpoint складские `materialItems` не принимает. UI приёмщика на 390/1280 выбирает товар/расходник и количество, не списывает их автоматически. [Полный контракт](APPOINTMENT_MATERIALS.md) и PG17/HTTP `test_pg_auto_booking_materials_atomic.py`, Playwright `auto-calendar.spec.ts`. Права WAREHOUSE не наследуются от INTAKE и тем более CLIENT; реальные цены/склады/юридическое согласие/production #119 остаются planned. #113 остаётся открытой до полного соблюдения AC.


## Часы работы филиала и закрытые даты (current-dev, #113)

`0093_auto_location_opening_hours`: MANAGER филиала атомарно настраивает до 28 недельных интервалов (ISO пн=1..вс=7, минуты 0..1440) и до 120 закрытых дат с проверкой пересечений, версий и будущих активных записей; история append-only, прямой SQL INSERT брони также под PostgreSQL guard. Staff/CLIENT candidate slots отсеивают время вне рабочих часов и возвращают `openingHoursConfigured` (для отсутствующей конфигурации — false + явный dev warning). POST создания/переноса возвращает `409 LOCATION_CLOSED` без phantom WorkOrder/stock/outbox. Разрешены 23/25-часовые IANA сутки, нельзя пересекать перерыв/закрытую дату. Управление доступно scoped MANAGER с CSRF; INTAKE может читать, CLIENT только искать часы через собственные слоты. PG17/HTTP: `test_pg_auto_location_opening_hours.py`; Playwright: `auto-calendar.spec.ts`; [полный контракт](OPENING_HOURS.md). Unconfigured филиалы сохраняют старый synthetic режим для совместимости, не production. Юридические/реальные данные, consent, pricing, holiday exception shifts и release gate #119 остаются нерешёнными.
