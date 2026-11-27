# Автомобильный склад — синтетическое ядро PG17

**Статус:** current-dev synthetic PG17. Реализованы отдельные закрытые loopback-only HTTP-сценарии инвентаризации, приёмки, оценки прихода и выдачи/возврата по заказ-наряду, а также защищённые dev UI инвентаризации, прихода, выдачи и пересортицы. **Публичный production API, полномочия непривилегированной роли PostgreSQL и полноценные интерфейсы всех складских операций — planned.** Родительские #115 и #168 остаются открытыми; перечисленные ниже этапы описывают исторический прогресс и не заменяют полный Definition of Done #168.

## Реально работает (#237)

Миграция `0052_auto_stock_ledger` создаёт отдельные от legacy-robot inventory сущности `auto_stock_sku` (организация, филиал, артикул), `auto_stock_event` (неизменяемые события) и вычисляемое представление `auto_stock_balance` (`on_hand`, `reserved`). `available = on_hand - reserved`; складской остаток никогда не выводится из предложения поставщика (#235) и не считается доказательством применяемости из каталога (#231/#233).

Внутренняя SQL-функция `record_auto_stock(sku_id, request_id, action, quantity)` принимает действия `RECEIPT`, `RESERVE`, `RELEASE`, положительное целое количество и UUID запроса. Транзакционная блокировка строки SKU сериализует операции, предотвращая двойной резерв. Повтор с тем же UUID и параметрами возвращает прежний event ID без повторного эффекта; изменённый запрос отвергается. SQL insert trigger дополнительно проверяет scope и отсутствие отрицательного доступного/зарезервированного количества, а UPDATE/DELETE истории запрещены. Журнал append-only; сумма поступлений и резервных событий проверяется в представлении.

**Важная граница:** функцию запрещено вызывать через публичный API; `EXECUTE` отозван у `PUBLIC`. DB owner в synthetic CI имеет привилегии и не рассматривается как конечный сотрудник. Нельзя давать application DB роли прямой доступ к INSERT/UPDATE/DELETE складских таблиц: до production нужно отдельное право tenant+branch+role, авторизация по действующей сессии, аудит работника, revoke/replay проверки и проверенная схема grants. Здесь нет приёмки реальных запчастей, бизнес-процесса закупок, себестоимости/НДС, возврата поставщику, партий/серий, инвентаризации или E2E кладовщика. Синтетическое ядро не считается завершением #168.

## Проверка

`PYTHONPATH=apps/api python -m pytest -q tests/integration/test_pg_auto_stock_ledger.py` с настроенным `ROBOport_DATABASE_URL` к одноразовой базе PostgreSQL 17 после Alembic upgrade. CI дополнительно проверяет upgrade→downgrade→upgrade и синтетический backup/restore. Тесты проверяют конкурентные резервы, rollback, идемпотентность, запреты UPDATE/DELETE, cross-tenant scope и прямые SQL-вставки с недостаточным остатком.

## Следующий dependency order

Роль WAREHOUSE, branch scope, авторизованный internal service и event actor → товарная карточка, партии и приходной документ → перемещения, выдача механику и возвраты → инвентаризация → UI, OpenAPI и негативные RBAC/BOLA/E2E проверки. До #119 запрещено объявлять этот модуль доступным в production.


## Авторизованные операции внутреннего сервиса (current-dev, #239)

Добавлен `apps/api/roboport_api/auto_stock_service.py` с `mutate_auto_stock(conn, token, organization_id, location_id, sku_id, request_id, action, quantity)`. Для **каждой** операции `RECEIPT` / `RESERVE` / `RELEASE` требуется действующая PostgreSQL-сессия сотрудника, активный `WAREHOUSE` grant организации/филиала и SKU именно этого scope. Проверка роли, сессии и запись ledger выполняются в одной транзакции с блокировками; отзыв прав должен дождаться уже начатой операции, а последующие вызовы запрещены. Legacy `user_role` и знание UUID запчасти не авторизуют складское действие.

Неверный вход, отсутствие полномочий или чужой SKU → `AutoStockDenied`; нехватка остатка, превышение резерва, конфликт idempotency key → `AutoStockConflict`. Не раскрываются личные данные, идентификаторы чужого филиала, содержимое токенов. Retry с тем же UUID допускается **только при действующей авторизации**, даже если событие когда-то было записано. Прямое исполнение SQL под привилегированным владельцем БД остаётся вне границы этого service-layer.

Проверка: `PYTHONPATH=apps/api python -m pytest -q tests/integration/test_pg_auto_stock_service.py` на одноразовой PostgreSQL 17. Рабочее место кладовщика, публичный HTTP/OpenAPI, разграничение SQL grants для production-пользователя БД, выдача по наряду, стоимость, документы и полный склад #168 остаются **planned**. Это внутренний тестируемый backend, а не пользовательская функция.


## Выдача запчастей по заказ-наряду (current-dev, #241)

Внутренний `mutate_work_order_stock` связывает живую сессию WAREHOUSE, tenant+филиал, активный `auto_work_order` и локальный SKU. В миграции `0053_auto_work_order_stock` операция `RESERVE` увеличивает общий резерв и резерв **конкретного заказ-наряда**, `ISSUE` одновременно уменьшает `on_hand` и `reserved`, а `RELEASE` освобождает ещё не выданное количество без уменьшения физического остатка. Нельзя списать деталь с чужого наряда или превысить его собственный резерв. События `auto_work_order_stock_event` append-only с idempotency через уникальный `request_id` общего ledger; повтор того же запроса возвращает прежний результат лишь после проверки действующих прав. Отмена записи, отзыв полномочий, отключение организации или чужой филиал запрещают новые операции.

Тест: `PYTHONPATH=apps/api python -m pytest -q tests/integration/test_pg_auto_work_order_stock.py` в одноразовой PostgreSQL 17. Проверены выдача, освобождение, rollback, конкурентная попытка двойного списания, idempotent retry и отрицательные права. Схема не раскрывает публичный API и не содержит себестоимость, НДС, акт выдачи, ответственного механика, пользовательский UI или производственный DB grant; это **current-dev** внутренняя операция, не релизная складская функция. Внешние поставщики и VIN применяемость остаются независимыми источниками данных.


## Возврат ранее выданной детали по наряду (current-dev, #243)

Синтетическая внутренняя операция `return_work_order_stock` использует действующую сессию и активную роль `WAREHOUSE` того же филиала, а также конкретный действующий `auto_work_order` + SKU. SQL `record_auto_work_order_return` атомарно создаёт неизменяемые события `RETURN` в общем и нарядном журналах. В `auto_stock_balance` возврат увеличивает `on_hand`, но **не увеличивает** `reserved`; количество ограничено суммой `ISSUE - RETURN` по тому же наряду и SKU. Повтор того же `request_id` и параметров не создаёт нового события и требует действующего доступа; конфликт возвращает доменную ошибку. Блокировка SKU сериализует конкурирующие операции. PostgreSQL deferred constraint требует парного события наряда и предотвращает одиночную SQL-вставку RETURN без связи.

Проверка: `PYTHONPATH=apps/api python -m pytest -q tests/integration/test_pg_auto_work_order_return.py` на одноразовой PG17. Операции возврата покупателем, деньги/возмещение, оценка состояния запчасти, партия, серийность, расходные документы, роль механика и публичный UI/API — **planned** в #115/#168/#170. Обратная миграция уничтожает записи возвратов и допускается только в disposable CI, не в production. Таблицы должны быть недоступны напрямую production application DB role; владелец БД остаётся привилегированным.


## Ячейки и партии автомобильного склада (current-dev, #245)

Синтетическая миграция `0055_auto_stock_bin_lot` добавляет `auto_stock_bin` с неизменяемыми организацией, филиалом и уникальным кодом ячейки, а также `auto_stock_lot` с конкретными SKU, ячейкой, batch-code и `source_revision`. Триггеры отвергают неверный филиал или отключённую организацию при создании и запрещают UPDATE/DELETE записей. Составной внешний ключ подтверждает, что ячейка партии принадлежит **тому же филиалу**; тесты проверяют и несовпадение SKU tenant. Сведения о `source_revision` относятся к внутреннему источнику партии, не доказывают OEM применяемость или лицензию.

Проверка: `PYTHONPATH=apps/api python -m pytest -q tests/integration/test_pg_auto_stock_bin_lot.py` на одноразовой PG17. Это **справочник идентичностей**, не количественный ledger по партиям: `auto_stock_balance` по-прежнему агрегирует по SKU, а `RECEIPT/RESERVE/ISSUE/RETURN` ещё не указывают `lot_id` и `bin_id`. Физические перемещения, контроль партий при выдаче, документы, авторизация пользователя при добавлении партии и интерфейс кладовщика — **planned** в #115/#168; использовать эти таблицы напрямую в production нельзя.


## Количественное размещение по партиям/ячейкам (current-dev, #247)

Миграция `0056_auto_lot_allocation` вводит append-only `auto_stock_lot_allocation` и вычисляемый `auto_stock_lot_balance` по `lot_id`/`bin_id`, операции `ALLOCATE` и `DEALLOCATE`, положительные целые количества, idempotency UUID и защиту от отрицательного размещения. **Это перераспределение уже существующего on_hand SKU, не новый приход и не дополнительное увеличение остатка.** `auto_stock_balance.on_hand` остаётся единственным физическим агрегатом; нельзя разместить больше `on_hand - sum(allocated)`. Защита на уровне SQL-триггеров и блокировки SKU предотвращает конкурентное двойное размещение, даже при прямой записи в синтетической БД. Обновлён guard физического `ISSUE`: существующая выдача может потреблять только ещё не размещённое количество. Перед физической выдачей размещённой партии необходимо отдельное корректное списание партии (здесь не реализовано). `RETURN` восстановит общий остаток, но не создаст партийное размещение без `ALLOCATE`.

Внутренний сервис `mutate_stock_lot_allocation` требует живую сессию и WAREHOUSE-grant организации/филиала в одной транзакции, запрещает чужой `lot_id` и повтор после отзыва полномочий. Нет публичного API, пользовательского UI, физического сканирования, резервирования/выдачи по lot ID, межфилиальных перемещений или production DB role: всё это **planned** в #115/#168. Тест: `PYTHONPATH=apps/api python -m pytest -q tests/integration/test_pg_auto_stock_lot_allocation.py` на одноразовой PG17. Downgrade уничтожает журнал размещения, только для disposable CI.


## Атомарные перемещения между партиями и ячейками (current-dev, #249)

`0057_auto_lot_transfer` реализует **внутрифилиальное** перемещение уже размещённого SKU: `record_auto_stock_lot_transfer(source_lot,destination_lot,request_id,quantity)` блокирует строку SKU и в одной транзакции создаёт две записи `auto_stock_lot_allocation` — `DEALLOCATE` из исходной партии и `ALLOCATE` в целевую — с неизменяемым `auto_stock_lot_transfer` (проверкой пары через deferred constraint). Количество в целевой партии растёт ровно на убывание исходной; общий `auto_stock_balance.on_hand` не меняется. Ошибки и отмена транзакции откатывают **обе** стороны. Границы: один SKU, одна организация, один филиал, разные `lot_id` и разрешённое количество на источнике; запрос с прежним UUID и теми же параметрами идемпотентен, с изменёнными параметрами конфликтует.

`auto_stock_lot_transfer.transfer_stock_lot` — внутренний Python-сервис с проверкой действующей сессии и `WAREHOUSE` grant по филиалу перед каждым запросом, в том числе при retry. Test: `PYTHONPATH=apps/api python -m pytest -q tests/integration/test_pg_auto_stock_lot_transfer.py` на disposable PG17. Это **current-dev**, не публичный HTTP и не реальный документ движения. Межфилиальный перевод, lot-level выдача по заказ-наряду, сканирование, пользовательский UI, производственные DB grants, себестоимость и инвентаризация остаются **planned** в #115/#168. Downgrade удаляет журнал перемещений, только для synthetic CI.


## Выдача из конкретной партии в заказ-наряд (current-dev, #251)

Миграция `0058_auto_lot_work_order_issue` вводит атомарную `record_auto_stock_lot_work_order_issue(order,lot,request,quantity)`. Под единой блокировкой SKU она списывает размещённое количество `DEALLOCATE` из указанной партии, проводит `ISSUE` через прежний защищённый ledger по SKU и заказ-наряду и сохраняет неизменяемую связку `auto_stock_lot_work_order_issue`. Физический `on_hand` и резерв уменьшаются на выданное количество ровно один раз; привязка всей цепочки проверяется отложенным PG constraint. Повтор с теми же параметрами идемпотентен после проверки прав, а конфликт параметров, нехватка партии или резерва откатывает все изменения.

Внутренний Python сервис `issue_work_order_from_lot` требует активные session и WAREHOUSE-grant того же филиала. Это `current-dev` synthetic PG17, без публичного HTTP, UI, производственного DB ACL, подписей механика и реальных складских документов. Тест: `PYTHONPATH=apps/api python -m pytest -q tests/integration/test_pg_auto_stock_lot_work_order_issue.py`. Возврат **обратно в конкретную партию**, инвентаризация и коммерческие операции остаются planned в #115/#168. Downgrade — только для disposable CI.


## Возврат выданных деталей обратно в исходную партию (current-dev, #253)

Миграция `0059_auto_lot_work_order_return` и внутренний сервис `return_work_order_to_lot` атомарно восстанавливают `auto_stock_balance.on_hand` и `auto_stock_lot_balance.allocated` конкретной исходной партии. `reserved` остаётся прежним. Отслеживаются связанная операция `ISSUE`, заказ-наряд, SKU, филиал, партия, UUID идемпотентности и неизменяемая пара `RETURN`+ `ALLOCATE` (deferred SQL constraint). Максимум возврата для пары (work_order_id, lot_id) равен сумме ранее выданного из этой партии за вычетом всех прежних возвратов в неё; чужую партию использовать нельзя. Общий возврат также проверяется существующим защищённым ledger. Все записи фиксируются в одной транзакции; при rollback обе стороны откатываются. Внутренний сервис проверяет живую сессию с WAREHOUSE grant того же филиала даже при повторе запроса.

PG17: `PYTHONPATH=apps/api python -m pytest -q tests/integration/test_pg_auto_stock_lot_work_order_return.py`. Это **current-dev synthetic PG17**, не production-документ возврата: нет инспекции качества, себестоимости, серийных номеров, публичного API, пользовательского UI, специальных полномочий DB role или гарантии приёма детали после закрытия наряда. Эти операции остаются planned в #115/#168. Downgrade уничтожает журнал, допускается только на disposable CI.


## Инвентаризация конкретной партии с утверждением расхождения (current-dev, #255)

`0060_auto_lot_stocktake` реализует два раздельных неизменяемых аудиторских события. `record_auto_stock_lot_count(lot,request,actual,reason)` снимает рассчитанное `expected` для партии под блокировкой SKU, фиксирует фактическое `actual` и обоснование, но **не изменяет остаток**. `approve_auto_stock_lot_count(count,request)` требует исходный остаток партии без изменений после подсчёта, не допускает вторичного применения и создаёт append-only approval. При положительном расхождении добавляет физический `COUNT_GAIN` и партию `ALLOCATE`; при отрицательном — `DEALLOCATE` и `COUNT_LOSS` в одной транзакции. `COUNT_LOSS` не может нарушить агрегатные резервы или существующее размещение других партий. Нулевое расхождение создаёт утверждение без событий остатков. Используются блокировка SKU, UUID идемпотентности, отложенное применение и целостный rollback.

Python `auto_stock_lot_stocktake` проверяет действующую сессию, WAREHOUSE grant и принадлежность партии организации/филиалу при подсчёте и утверждении. PG17: `PYTHONPATH=apps/api python -m pytest -q tests/integration/test_pg_auto_stock_lot_stocktake.py`. Это **current-dev synthetic PG17**: одно WAREHOUSE-полномочие на обе операции, пока нет отдельного независимого утверждающего, электронной подписи, полевого сканера, публичного API/UI, production DB ACL и инвентаризационного документа. Эти требования остаются planned в #115/#168. Downgrade удаляет события только в disposable CI; production rollback требует сохранения аудита.


## Независимое подтверждение инвентаризации (current-dev, #257)

Миграция `0061_auto_stocktake_dual_control` добавляет неизменяемые поля `auto_stock_lot_count.counted_by` и `auto_stock_lot_count_approval.approved_by` со ссылкой на `app_user`. Создание снимка возможно только при живой сессии и WAREHOUSE-grant филиала; применение расхождения — только при живой сессии и MANAGER-grant того же филиала. Сервис под lock session/grant передаёт user ID через транзакционный `roboport.stocktake_actor`; BEFORE INSERT triggers фиксируют ID реального вызывающего в записи и отклоняют отсутствие контекста, а также совпадение создателя и утверждающего даже при наличии у пользователя обеих ролей. Повторный запрос после отзыва роли не даёт доступа; UPDATE/DELETE аудиторских записей запрещены. Счётчик не может сам утвердить собственную инвентаризацию.

Это **internal current-dev** и не полная production RBAC: владелец БД с произвольным SQL теоретически может подменить сессионный параметр; для production требуются выделенные DB роли с ограниченными EXECUTE/табличными правами, не владеющие объектами. Публичного HTTP/API/UI пока нет. PG17: `PYTHONPATH=apps/api python -m pytest -q tests/integration/test_pg_auto_stocktake_dual_control.py tests/integration/test_pg_auto_stock_lot_stocktake.py`. Downgrade удаляет actor provenance только на disposable test DB; production downgrade без сохранения аудита запрещён. Родительские #115 и #168 остаются открытыми.


## Закрытый HTTP процесс инвентаризации (current-dev, #259)

Два dev-only POST маршрута работают только при `ROBOport_SYNTHETIC_AUTO_API=1`, disposable PostgreSQL на loopback, прямом HTTPS loopback origin и отключённом непроверенном proxy ingress: `/api/v1/dev/auto/organizations/{org_id}/stocktake/counts` принимает `{locationId,lotId,actual,reason}`; `/api/v1/dev/auto/organizations/{org_id}/stocktake/approvals` принимает `{locationId,countId}`. Оба требуют `Idempotency-Key` UUID, session cookie `rp_session`, session-bound `X-CSRF-Token`, Origin и действующую роль по конкретному филиалу. Создание снимка требует WAREHOUSE; утверждение — MANAGER (другое лицо). Ответ 201 с `countId` либо `approvalId` и `Cache-Control: no-store`. Ошибки fail-closed (403, 409, 422), неизвестный disabled маршрут — 404. Повтор действительного UUID возвращает прежний идентификатор, если полномочия не отозваны. Проверены tenant/branch BOLA, self-approval, связь утверждения с неизменяемым аудитом и корректировки количества на реальной PG17.

Маршруты `include_in_schema=False`, **не production API** и не разрешение работать с реальными складскими данными: отсутствуют отдельная непривилегированная production DB роль, публичный ingress, UI и согласование производственного статуса. Проверка: `PYTHONPATH=apps/api python -m pytest -q tests/integration/test_pg_auto_stocktake_http.py`. Родительские #115/#168 продолжаются до внедрения production контроля доступа.


## Межфилиальные физические перемещения (current-dev; часть #168)

`0062_auto_interbranch_transfer` и внутренний `transfer_interbranch_stock` выполняют **одну атомарную операцию** между двумя включёнными филиалами той же организации. Исходная и целевая партии ссылаются на разные локальные SKU с **одинаковым артикулом**. Источник уменьшает партию (`DEALLOCATE`) и физический остаток (`TRANSFER_OUT`); получатель увеличивает физический остаток (`TRANSFER_IN`) и целевую партию (`ALLOCATE`). Неизменяемый `auto_stock_interbranch_transfer` связывает все четыре проводки; PG17 deferred constraint проверяет парность и scope. Строки обоих SKU блокируются в фиксированном порядке, что сериализует встречные перемещения. Доступный на источнике `on_hand - reserved` должен покрывать количество — чужие резервы перемещение не расходует. Любая ошибка или rollback отменяет весь перевод без временного удвоения товарного остатка. Повтор UUID идемпотентен, но допустим только при **двух действующих WAREHOUSE-grant**: исходного и целевого филиалов.

Тесты: `PYTHONPATH=apps/api python -m pytest -q tests/integration/test_pg_auto_stock_interbranch_transfer.py` с одноразовой PostgreSQL 17. Это **current-dev, не весь scope #168**: отсутствуют транспортная накладная и статусы "отгружено/в пути/принято", стоимость и НДС, приемка механиком, сканирование, пользовательский HTTP/UI, производственный least-privilege DB role. Для настоящих складских перемещений нужен отдельно утверждённый процесс доставки и инвентаризации по точкам. Родительская #168 остаётся открытой до выполнения полного scope. Downgrade здесь допустим только для disposable CI, так как уничтожает журнал операций.


## Списания и возврат запчастей поставщику (current-dev; часть #168)

`0063_auto_stock_disposition` реализует две физические операции `WRITE_OFF` и `SUPPLIER_RETURN`. Внутренняя `dispose_stock_lot` требует живую сессию и WAREHOUSE-grant филиала; для возврата поставщику обязателен `supplier_reference` длиной 3–160 символов, для обеих операций — reason 3–240 символов, positive quantity и UUID идемпотентности. Под lock SKU в одной транзакции выполняются `DEALLOCATE` исходной партии и уменьшение физического SKU посредством `WRITE_OFF` или `SUPPLIER_RETURN`. Невозможно списать размещённое количество сверх партии либо уменьшить `on_hand` ниже `reserved`; уникальный запрос не допускает двойное списание. Immutable `auto_stock_lot_disposition` хранит действие, причину, ссылку поставщика и обе проводки с deferred проверкой сопряжённости. Отмена транзакции возвращает оба остатка.

PG17: `PYTHONPATH=apps/api python -m pytest -q tests/integration/test_pg_auto_stock_disposition.py`. **Только current-dev**: ссылка на поставщика не означает оформленную претензию или денежный возврат. Качество детали, контрагент, цены/НДС, формальные утверждения, кредит-нота, гарантия, транспорт, production DB grants, HTTP/OpenAPI/UI и документы выдачи остаются planned в #168/#115. Нельзя применять к реальным складам, downgrade допустим лишь для одноразового CI.


## Интерфейс инвентаризации (current-dev, часть #168)

`apps/web/src/pages/staff/WarehouseStocktake.tsx` подключён в `/staff/warehouse-stocktake` с навигацией «Инвентаризация (dev)»; два защищённых HTTP шага выполняются с session+CSRF и отдельными UUID Idempotency-Key. На неопределённый HTTP/сетевой исход форма блокирует новый запрос до безопасного повтора с **прежним UUID**, а при смене org/location очищает отложенный запрос. Успешный подсчёт показывает countId; отдельный MANAGER утверждает корректировку. Playwright: `apps/web/e2e/warehouse-stocktake.spec.ts`. Подробная русская инструкция: [Кладовщик и закупщик](../guides/warehouse.md). Это **только synthetic dev**, остальные объекты и сценарии полного #168 остаются planned.


## Просмотр партий для выбора инвентаризируемой позиции (current-dev, часть #168)

Внутренний `auto_stock_lot_read.list_stocktake_lots` и закрытый GET `/api/v1/dev/auto/organizations/{org_id}/stocktake/lots` возвращают ограниченную страницу (1–50) только партий указанного филиала и организации с live WAREHOUSE/MANAGER authorization, scoped SQL joins и keyset UUID cursor. Ответ: `lotId,article,batchCode,binCode,allocated,onHand,reserved`; цены, поставщики и персональные данные не раскрываются. Веб-форма может выбрать партию из списка, не требует ручного UUID. PG17 HTTP тесты: `tests/integration/test_pg_auto_stock_lot_read_http.py`; Playwright: `apps/web/e2e/warehouse-stocktake.spec.ts`. Повтор после отзыва прав не возвращает старый список. Только synthetic dev, production DB access/UI не заявлены.


## Двухэтапный приходный документ (current-dev, часть #168)

Миграция `0064_auto_receipt_document` создаёт immutable `auto_stock_receipt_document` (партия, филиал, количество, номер документа поставщика, автор) и `auto_stock_receipt_acceptance` (другой утверждающий, парные stock/lot events). Внутренний сервис `create_receipt_document` требует текущую роль WAREHOUSE; `accept_receipt_document` — текущую MANAGER того же филиала и иного пользователя. На этапе draft **остатки не меняются**; единственное подтверждение атомарно записывает физический `RECEIPT` и партийный `ALLOCATE`. Повторы запроса UUID безопасны лишь при активной сессии и наличии прав. Одинаковый пользователь с обеими ролями не может принять собственный документ. Аудит неизменяем, PG17 deferred constraint проверяет связанность проводок; rollback не оставляет частичных приходов. Тесты: `tests/integration/test_pg_auto_stock_receipt_document.py` (scope, actor, revoke, concurrency, rollback). Только synthetic current-dev; транспортный приход, цены/НДС, лицензированный заказ поставщику, production DB ACL, другие документы и полный документный workflow ещё planned. Не является закрытием #168.


### Dev HTTP и экран приёмки партии

Закрытые синтетические маршруты с живой session+CSRF: `POST /api/v1/dev/auto/organizations/{org_id}/warehouse/receipts` для WAREHOUSE принимает `{locationId,lotId,quantity,sourceReference}`, возвращает `documentId`; `POST .../warehouse/receipts/accept` для независимого MANAGER принимает `{locationId,documentId}`, возвращает `acceptanceId`. Оба требуют UUID заголовок `Idempotency-Key`, статус 201 и no-store, 403 по правам, 409 при конфликте или повторной приёмке, 422 при неверном теле. Форма `/staff/warehouse-receiving` (меню «Приход (dev)») содержит выбор партии, черновик и подтверждение другой сессией; неизвестный исход блокирует новое действие до повтора с исходным ключом. Тесты: `tests/integration/test_pg_auto_receipt_http.py`, `apps/web/e2e/warehouse-receiving.spec.ts`. Не production API, лицензированный заказ поставщику и юрзначимые документы по-прежнему planned.


## Себестоимость закупки и НДС отдельно от движения склада (current-dev, часть #168)

Миграция `0065_auto_receipt_valuation` вводит immutable `auto_stock_receipt_valuation`, связанную только с **принятым** приходным документом. Действующий MANAGER филиала, отличающийся от автора приходного документа, сохраняет валюту в виде трёх букв, закупочную цену **за единицу в minor units**, сумму входящего НДС по всему документу **отдельным полем в minor units**, ссылку на ревизию предложения поставщика и точное время источника с timezone. Нет пересчёта курса, расчёта налоговой ставки или предположения о лицензированной применяемости. Проекция `auto_stock_receipt_valuation_report` вычисляет чистую закупочную стоимость `qty*unit_net_minor`, отдельный `vat_total_minor` и их сумму; эти данные **никогда не записываются в физический ledger и не являются внутренней розничной ценой**. Автор, источник, время и idempotency UUID сохраняются в append-only; повтор после отзыва полномочий запрещён.

Только закрытый synthetic HTTP `POST /api/v1/dev/auto/organizations/{org_id}/warehouse/receipts/valuation`: `{locationId,documentId,currency,unitNetMinor,vatTotalMinor,supplierPriceRevision,supplierQuotedAt}`; заголовки session+CSRF+`Idempotency-Key`. Результат `valuationId`; 403 нет роли, 409 конфликт/двойная оценка, 422 некорректные единицы/данные, 404 если dev выключен. Экран `/staff/warehouse-receiving` после приёмки имеет шаг 3 для данных покупки/НДС с безопасным повтором. Тесты: `tests/integration/test_pg_auto_stock_receipt_valuation.py`, `tests/integration/test_pg_auto_receipt_valuation_http.py`, `apps/web/e2e/warehouse-receiving.spec.ts`. Денежные корректировки, документы списания по себестоимости, бухгалтерские проводки, законная ставка налога, серийность, закупки и production DB ACL остаются **planned**, #168 не закрывать.


## Закрытый HTTP выдачи и возврата из партии по наряду (current-dev, часть #168)

Внутренние защищённые POST маршруты `/api/v1/dev/auto/organizations/{org_id}/warehouse/work-orders/issue` и `/return` используют общую строгую схему `{locationId,workOrderId,lotId,quantity}` и обязательный UUID `Idempotency-Key`. Сначала действующая сессия+CSRF, затем WAREHOUSE полномочие **конкретного филиала**, совпадение tenant/location у заказ-наряда и партии. `issue_work_order_from_lot` требует предварительный резерв **этого наряда**, атомарно списывает партию+SKU+наряд; `return_work_order_to_lot` не позволяет вернуть больше ранее выданного по этой партии/наряду. Retry после отзыва роли запрещён; 201 `issueId`/`returnId`, 403 отсутствующая роль, 409 конфликт, 422 неверный payload; no-store. PG17 HTTP тесты `tests/integration/test_pg_auto_workorder_lot_http.py`. Маршруты synthetic-only и не входят в production OpenAPI; оформление полномочий механика, акты подписания, полноценный публичный пользовательский UI остаются planned.


## Пересортица номенклатуры — current-dev, migration 0070 (#168)

Добавлены два независимых шага: `auto_stock_reclassification_document` (WAREHOUSE, черновик без проводок) и `auto_stock_reclassification_acceptance` (MANAGER другой сотрудник). Утверждение меняет **обе** номенклатуры и обе партии в одной транзакции: `DEALLOCATE` исходной партии, `RECLASS_OUT` исходного SKU, `RECLASS_IN` целевого SKU, `ALLOCATE` целевой партии. Суммарное физическое количество остаётся неизменным. Поля `reason`, `created_by`, `accepted_by` и UUID запросов — неизменяемый аудиторский след; одна из двух операций не может завершиться без другой. Транзакционная блокировка обеих SKU в детерминированном порядке, проверка нерезервированного остатка и партионного размещения предотвращают гонки. Сериализованные исходные партии отклоняются: нет поэкземплярной реклассификации, поэтому запрещено менять SKU без отдельного serial movement ledger.

Доступно **только** через opt-in synthetic loopback HTTP и UI: `POST /api/v1/dev/auto/organizations/{org_id}/warehouse/reclassifications` (поля `locationId,sourceLotId,destinationLotId,quantity,reason`), затем `.../reclassifications/approvals` (поля `locationId,documentId`), `/staff/warehouse-reclassification`. `Idempotency-Key`, CSRF и независимые живые филиальные права обязательны. Контракт исключён из production OpenAPI сознательно, не выдавать за открытый API. В production остаются непроверенные least-privilege DB role, per-unit movement, cost allocation, документы и gate #119; #168 OPEN.


## Серийные движения WorkOrder/RETURN/TRANSFER — current-dev, migration 0071 (#168)

После регистрации `auto_stock_serial` синтетический модуль ведёт **неизменяемые поэкземплярные движения** в `auto_stock_serial_movement`: `ISSUE` по существующему автомобильному заказ-наряду, `RETURN` с восстановлением партии и `TRANSFER` между филиалами с тем же артикулом. Каждое движение ровно одной единицы ссылается на одну реальную запись количественного ledger (`auto_stock_lot_work_order_issue`, `..._return` либо `auto_stock_interbranch_transfer`); переход и физический остаток коммитятся атомарно. Текущее местоположение, SKU, партия, заказ-наряд и состояние рассчитываются из append-only журнала в `auto_stock_serial_current` по монотонному номеру события (а не времени транзакции).

Движения защищены живой ролью `WAREHOUSE` в исходном филиале и для межфилиального переноса — **в обоих** филиалах; исходный филиал указан явно. Повтор UUID после перевода проверяет исходную ветку, оба права и прежнего актёра. Запрет на одиночное количественное списание зарегистрированной серийной единицы обеспечен deferred guard общего lot allocation: число присутствующих серий в партии не может превышать её размещённый остаток. Отдельные физические незарегистрированные единицы по-прежнему могут двигаться количественным способом; полный серийный контроль требует отдельной политики обязательной регистрации для маркированных SKU. Не допускается перетаскивать серийные номера через пересортицу, списание и другие операции без собственных linked serial events.

Синтетические маршруты: `POST /api/v1/dev/auto/organizations/{org_id}/warehouse/serial-movements/{issue|return|transfer}`. Для issue/return JSON `{locationId,serialId,workOrderId}`, для transfer `{locationId,serialId,destinationLotId}`; обязательны CSRF, активная cookie сессии, `Idempotency-Key`. Интерфейс `/staff/warehouse-serial-movements`. Указанные dev-маршруты скрыты из публичного OpenAPI и не подтверждают production readiness.

Не реализованы customer/supplier serial returns с юр. документами, отслеживание маркированных SKU в procurement, полная production tenant DB ACL, себестоимость выдачи и legal gate #119. Issue #168 остаётся OPEN.


### Принудительные инварианты после SQL-review (#168)

Проверка количества зарегистрированных серий в партии выполняется **при каждой регистрации** по `auto_stock_serial_current` и при каждом партионном изменении отложенным SQL constraint trigger. Таким образом, серийный номер, перемещённый из другого филиала, учитывается в целевой партии, а обычное списание не может «удалить» его физически без серийного события. Дубликаты блокируются для текущего SKU **во всех его партиях** как при перемещении, так и при новой регистрации, включая номера, сейчас выданные по наряду. Отдельный deferred trigger запрещает количественный возврат по WorkOrder, который оставил бы необеспеченные физическими выдачами серийные единицы в статусе `ISSUED`. Отрицательные тесты создают конкурирующие ситуации через прямой SQL и обходят сервисные проверки, чтобы тестировать именно БД.


### Защита от исторических проводок и HTTP 409

Прямая запись движения серийной детали ссылается на конкретную количественную проводку, но эта проводка может быть исторической. Поэтому отдельный **deferred movement-balance trigger** повторно проверяет фактическую ёмкость исходной и целевой партии и непогашенные выдачи по заказ-наряду в момент COMMIT, даже если никакие новые lot allocation события в транзакции не возникли. Для обычного HTTP возврата сервис инициирует проверку serial outstanding constraint **внутри защищённой транзакции**, чтобы запрещённый количественный возврат стабильно отвечал 409, а не утечкой SQL ошибки/500. Есть PG17 и HTTP регрессионные тесты. До production по-прежнему нужна непривилегированная role/tenant ACL и gate #119.


### HTTP 409 при неразрешённой количественной выдаче

Если в партии невозможно количественно выдать деталь без нарушения учёта серийных экземпляров, API обычной складской выдачи немедленно проверяет отложенный PostgreSQL trigger **в защищённом savepoint** и возвращает `409 CONFLICT`, а не поздний `500` на COMMIT. Это симметрично проверке количественного возврата. Прямые SQL вызовы по-прежнему отклоняются на уровне DB constraint. PG17 и HTTP регрессионные тесты проверяют оба пути.


### Нормализованные конфликты серий во всех обычных складских операциях

Новый deferred guard должен быть видим через понятный `409 CONFLICT` во **всех** сервисах, которые могут уменьшать размещённый остаток партии: инвентаризация/утверждение, ручное снятие размещения, внутрискладское и межфилиальное перемещение, списание/возврат поставщику, пересортица, выдача по WorkOrder. Каждый такой сервис теперь принудительно запускает constraint до выхода из защищённого savepoint, переводя нарушение в штатный отказ, а не в позднюю HTTP 500 на COMMIT. Связанные операции с правильно зарегистрированным serial movement по-прежнему используют общую транзакцию и проходят deferred проверку в конце. PG17 и HTTP тесты подтверждают откат и 409 при попытке неучтённого движения сериализованной единицы.


## Независимый возврат поставщику и закупочная оценка (current-dev, migration 0072)

Двухэтапный синтетический документооборот: `auto_stock_supplier_return_document` связывает складскую партию с конкретным **принятым и оценённым приходом**, количеством, причиной и внешним номером обращения поставщику. Сотрудник `WAREHOUSE` оформляет акт без изменения остатков. Другой сотрудник с `MANAGER` того же филиала принимает документ; подтверждение атомарно создаёт `SUPPLIER_RETURN` и `DEALLOCATE` через существующий физический/лот ledger и неизменяемую `auto_stock_supplier_return_acceptance`. Проверяется серийное ограничение, запрет списать резерв, живая авторизация и идемпотентность запросов.

**Отдельная финансовая атрибуция, не денежная выплата:** принятая входящая стоимость и входной НДС считаются только по `auto_stock_receipt_valuation` исходного прихода. `purchase_net_attributed_minor = unit_net_minor × quantity`; распределение входного НДС — разница накопленных целочисленных долей исходного `purchase_vat_total_minor`, чтобы два частичных возврата не округляли одну и ту же копейку дважды. На одну оценённую партию нельзя зачесть возвраты более исходного приходного количества, даже если на складе уже есть новые поставки. Не пересчитываются продажная цена, НДС к возмещению, кредит-нота или признание поставщиком.

Доступен закрытый синтетический API `POST /api/v1/dev/auto/organizations/{org_id}/warehouse/supplier-returns` с `locationId,sourceReceiptId,quantity,reason,supplierReference`; утверждение `.../supplier-returns/approvals` с `locationId,documentId`. Оба шага требуют CSRF, `Idempotency-Key`, действующей сессии/branch grant. Экран `/staff/warehouse-supplier-returns`. Исторический прямой `record_auto_stock_lot_disposition` остаётся synthetic legacy route, поэтому этот процесс **не является обязательным для каждого возможного supplier return в БД**. Production accounting/credit note, source-lot provenance для смешанных приходов, права непривилегированной DB роли, реальные заказы поставщиков и юридический gate #119 — planned; исходная #168 OPEN.


## Обзор складских выдач для руководителя (current-dev, #171)

Действующие append-only `auto_stock_event` действия
`ISSUE` и `RETURN` теперь используются
**только для чтения** в управленческом
`/staff/auto-stock-abc-xyz`.
Доступ: live scoped MANAGER; это не новое
право WAREHOUSE, не изменение остатков,
не бухгалтерская себестоимость
и не расчёт заказа поставщику.
Доступны 3–12 полностью завершённых
локальных месяцев, классификация
ABC по валовому количеству выдач
и XYZ по их месячной вариации,
пагинация по 25 SKU с общими итогами.
`RETURN` учитывается в отдельном
показателе периода без искажения XYZ.
[Полный словарь и PG17/Playwright](REPORTING.md).


## Очередь ожидающих инвентаризаций — synthetic dev (#168)

Read-only endpoint:

GET /api/v1/dev/auto/organizations/{org}/stocktake/pending?locationId={loc}&limit=20&afterCountId={uuid}

Возвращает не более 20 записей и nextCountId (или null),
syntheticOnly=true; Cache-Control:no-store. Сортировка по
UUID count_id, keyset-pagination. Строка содержит countId,
lotId, article, batchCode, binCode, expected, actual,
currentAllocated, stale, reason; actor UUID, idempotency key
и персональные данные не отдаются. Нет непроверенных
межфилиальных записей: SQL включает организацию, филиал,
активность и отсутствие записи об утверждении, а live
MANAGER authorization выполняется до чтения. WAREHOUSE
без MANAGER, чужой филиал, отозванная роль → 403;
неверный UUID или limit вне 1..50 → 422. Только HTTPS
loopback; production OpenAPI и миграции не меняются.

Руководитель выбирает countId из очереди, но сам POST
/stocktake/approvals **не доверяет** очереди и заново
проверяет права, независимость maker/checker, неустаревший
expected и целостность запаса. stale=true лишь подсказка
для обновления подсчёта; не гарантия успешного approve.
Утверждение переводит расхождение в immutable ledger,
затем UI обновляет очередь; счёт исчезает. Отзыв доступа
очищает данные. PG17 и Playwright проверяют 403/422,
пагинацию, утверждение, устаревший count, no PII и revoke.
Полный склад #168 и release gate #119 остаются OPEN.


## Очереди на приёмку и оценку приходов (current-dev, #168)

Новый закрытый read-only HTTP контракт:

```text
GET /api/v1/dev/auto/organizations/{org}/warehouse/receipts/pending?locationId={loc}&stage=accept&limit=20
GET /api/v1/dev/auto/organizations/{org}/warehouse/receipts/pending?locationId={loc}&stage=value&limit=20&afterDocumentId={uuid}
```

`stage=accept`: ещё не подтверждённый документ без записи в `auto_stock_receipt_acceptance`. `stage=value`: подтверждённый, но ещё не оценённый документ (нет `auto_stock_receipt_valuation`). Доступ только действующему MANAGER данного филиала; SQL ограничен организацией, филиалом и их активностью. Ответ `Cache-Control:no-store`: `syntheticOnly`, `stage`, `items[]`, `nextDocumentId`. Каждая строка содержит `documentId`, `lotId`, `article`, `batchCode`, `binCode`, `quantity`, `sourceReference`, но не персональные ID сотрудников, клиента, VIN и ключи повторов.

Сортировка по UUID документа, cursor-pagination до 20 по умолчанию (1–50), не по времени создания. Отозванные права и чужой scope → 403, неверный UUID/limit/stage → 422. Обе очереди перечитываются после успешных POST. Возможны гонки между чтением и мутацией; серверный POST независимо проверяет другого создателя, актуальную роль, физическую атомарность и отсутствие повтора. Выбор в очереди **не** является разрешением на запись. Фиксация оценки отдельно от физического остатка. Все транзакционные проверки и idempotency сохранены.

Контрактные PG17 тесты: `tests/integration/test_pg_auto_receipt_http.py`; Playwright: `apps/web/e2e/warehouse-receiving.spec.ts`. Нет миграций или production OpenAPI; synthetic-only до #119.


## Очередь возвратов поставщику для MANAGER — synthetic dev (#168)

```text
GET /api/v1/dev/auto/organizations/{org}/warehouse/supplier-returns/pending?locationId={loc}&limit=20&afterDocumentId={uuid}
```

Read-only private `GET` с `Cache-Control:no-store`;
`syntheticOnly=true`, `items[]`, `nextDocumentId`.
В каждой строке `documentId`, `sourceReceiptId`, `lotId`,
`article`, `batchCode`, `binCode`, `quantity`, `reason`,
`supplierReference`. Личных UUID авторов, клиентов, VIN,
ключей идемпотентности и платёжных реквизитов нет.

SQL читает только активные документы данного org/location,
которые опираются на принятый и оценённый входной приход,
**ещё не утверждены** и были созданы другим сотрудником.
Live MANAGER grant и личность текущей сессии проверяются
**до выборки**. Пагинация по UUID документа: 20 по умолчанию,
1–50 разрешено, курсор `afterDocumentId`. Не заявляется
хронологический порядок или атомарный снимок между страницами.
Отзыв роли/чужой филиал/нет сессии → 403; некорректные
UUID и limit → 422. GET **никогда** не выполняет списание.

Выбор документа из UI — не разрешение на POST: действие
`/warehouse/supplier-returns/approvals` заново проверяет
разделение WAREHOUSE/независимого MANAGER, неизменяемые
документы, уникальность idempotency key, источник закупочной
стоимости, наличие товара и серийный учёт в транзакции.
Утверждённый акт не возвращается в очередь. После POST
UI перечитывает очередь с сервера и очищает старый выбор.
PG17: `tests/integration/test_pg_auto_stock_supplier_return_http.py`;
Playwright: `apps/web/e2e/warehouse-supplier-returns.spec.ts`.
DDL/публичный production API не меняются. Это не кредит-нота,
реальная выплата поставщика или юридически принятый НДС;
релизная задача #119 остаётся OPEN.


## Межфилиальное перемещение: рабочее место и подтверждение (current-dev, #168)

Экран `/staff/warehouse-interbranch-transfers` позволяет кладовщику с действующим WAREHOUSE обоих филиалов одной организации выбрать направление, найти артикул, выбрать исходную и существующую целевую партию того же артикула, указать количество и явно подтвердить фактическую передачу. Доступное количество — минимум размещённого остатка и незарезервированного SKU, предварительная подсказка; атомарный SQL guard проверяет актуальный остаток, serial allocation и scope при записи. Существующие четыре проводки TRANSFER_OUT/IN + DEALLOCATE/ALLOCATE сохраняются без дублирования. Новая immutable таблица `auto_stock_transfer_receipt` фиксирует автора HTTP-операции в одной транзакции; старые внутренние движения не получают вымышленного автора. 201 новый receipt, 200 точный повтор тем же автором и ключом, 403 без обоих grants, 409 конфликт количества/артикула/ключа, 422 неверные данные. GET и POST `/api/v1/dev/auto/organizations/{org}/warehouse/interbranch-transfers`, GET принимает sourceLocationId/destinationLocationId, q и session-bound cursor. Независимые страницы исходных и целевых партий по 50 записей, поиск артикула; журнал по 50 строк с отдельным курсором. Записи раскрывают debit/credit stock UUID. Отказ очищает данные, неизвестный исход допускает точный retry. Миграция 0137 добавляет подтверждения обеих разновидностей движения, без изменения прежних проводок; downgrade пустой схемы допустим, иначе нужен backup/restore. Это фиксация уже физически переданной партии, не двухэтапная логистика в пути, не юридический транспортный документ и не бухгалтерская переоценка. Production #119 остаётся закрыт.


**Перемещение между ячейками:** тот же экран, режим LOCAL, один филиал, WAREHOUSE именно этого филиала. Выбираются две разные партии одного SKU. Создаются только неизменяемые DEALLOCATE/ALLOCATE и отдельный receipt автора `auto_stock_local_transfer_receipt`; физический on_hand и общий reserve не меняются, поэтому доступное для такого перемещения количество — размещённый остаток, а не on_hand−reserved. Ограничения серийности сохраняются. API LOCAL требует равенства sourceLocationId/destinationLocationId, INTERBRANCH — различия. Независимые sourceCursor/destinationCursor привязаны к стороне, направлению, режиму, поиску и сессии; продолжение одной стороны сохраняет выбор другой, если партия ещё доступна. Журнал использует отдельный cursor, в LOCAL показывает UUID размещений, в INTERBRANCH физические события. При смене режима/филиала начинается первая страница. PG17 покрывает 52+52 партии одного артикула, полное покрытие, подмену курсоров, локальное движение зарезервированного SKU без изменения физических остатков, повтор и отзыв обеих ролей. Это текущий синтетический учёт фактического перемещения, не логистика в пути.


## Акт списания запчастей (current-dev, #168)

Рабочее место `/staff/warehouse-writeoffs`: WAREHOUSE выбирает партию, количество, основание (повреждение, истечение срока хранения, непригодность, другое) и синтетическое обоснование. Черновик неизменяем, не резервирует товар и не изменяет остатки. Независимый MANAGER своего филиала утверждает или отклоняет; автор с действующим WAREHOUSE может отменить ожидающий акт. Только утверждение атомарно создаёт WRITE_OFF и DEALLOCATE с неизменяемым решением, связью disposition и обеими проводками; повтор с тем же автором/ключом/телом возвращает receipt, иное решение или повтор другого автора конфликтует. Резерв защищён; при изменении доступного количества или серийном ограничении решение не записывается. Отклонение/отмена — терминальные решения без движения. Для исправления создаётся новый акт, не редактируется история. Журнал содержит статусы, причину, авторов, время и UUID проводок; независимые session-bound cursor для партий и документов, фильтр статуса и поиск артикула/партии, до 50 строк на страницу.

GET/POST `/api/v1/dev/auto/organizations/{org}/warehouse/writeoffs`, POST `.../{documentId}/decisions`: live branch WAREHOUSE/MANAGER, CSRF/Idempotency-Key, 201/200/403/409/422, no-store. 0138 добавляет две append-only таблицы, guard связи акта с исходной партией и решением, downgrade только пустой истории. Старый внутренний `dispose_stock_lot` остаётся synthetic API для тестов, не выдаётся за новый акт. Нет бухгалтерской оценки, налоговой корректировки, юридической подписи, реальных ПДн или производства до #119. Проверки расширяют существующую PG17 fixture и проверяют независимые решения, отмену/отказ, конкуренцию, неизменяемость, резерв и отзыв, E2E интерфейса. #168 не закрывается до полного исходного DoD.


## Сверка складских остатков и детализация (current-dev, #168)

WAREHOUSE или MANAGER активного филиала открывает `/staff/warehouse-reconciliation`: сводка числа артикулов с расхождениями, неразмещённым количеством, резервом и нулевым свободным остатком; поиск по артикулу и фильтр. По каждому SKU — физический остаток, резерв, размещение, неразмещённый и свободный остаток, признак нарушения инвариантов. Неразмещённый остаток сам по себе не ошибка. Сводка и страница рассчитаны одним SQL statement из существующего `auto_stock_reconciliation`; количество передаётся точной целочисленной строкой, нет агрегирования несопоставимых артикулов. Детализация до партии/ячейки и исходных физических событий и резервов читается отдельным запросом; это не исторический snapshot на дату сводки. GET `/api/v1/dev/auto/organizations/{org}/warehouse/reconciliation?locationId=...&q=...&status=...&cursor=...`, GET `.../reconciliation/{skuId}?locationId=...&kind=lots|events&cursor=...`; 50 строк, отдельные подписанные session/scope/filter cursors, 403/422, no-store. Без новых таблиц, мутаций, бухгалтерской оценки или production доступа. При расхождении требуется расследование и штатная инвентаризация, не ручное изменение ledger. PG17 расширяет существующую выдачу/возврат, 52 SKU и партии, 50+ движений, scoped detail, filter/cursor/revoke; E2E сводка, детализация и отказ. #119 остаётся gate.


**Коммерческая детализация сверки:** управляющему с действующими MANAGER и SUPPLIER_ORDER READ/PRICE_PURCHASE (учитываются DENY всех активных MANAGER/WAREHOUSE, исторический cutoff блокирует полный журнал) доступны виды `kind=receipts` и `kind=supplier-returns` в том же GET детализации SKU. Входящие документы показывают DRAFT/ACCEPTED/VALUED, исходную партию/приёмку, точную цену, валюту, НДС, источник и накопленные подтверждённые возвраты по исходному приходу. Возвраты показывают исходный документ, причину, подтверждение и атрибутированные net/VAT; черновик не имеет финансового результата. Суммы передаются строками в minor units, валюты не смешиваются. Физическая сверка остаётся доступна без закупочных цен. Каждый запрос повторно проверяет полномочия; deny очищает экран. Движения RECLASS_IN/OUT отображаются наряду с остальными. Это сверка входящих документов, не оценка текущего остатка, себестоимость списания или бухгалтерский налоговый расчёт. PG17 проверяет точное распределение 601 minor НДС на 200+401, статусы прихода, личный DENY при двух ролях и cutoff; E2E охватывает финансовую детализацию и пересортицу. Миграций нет.


## Единый контроль закупочных документов (#168/#446, current-dev)

Прямые операции закупки, приёмки, оценки и возврата поставщику повторно применяют SUPPLIER_ORDER:READ на живых MANAGER/WAREHOUSE полномочиях; DENY любой применимой активной роли запрещает операцию. Создание черновика — CREATE (+WRITE), физическое независимое подтверждение — CLOSE (+WRITE), оценка — EDIT (+WRITE) и PRICE_PURCHASE. Ограничения не дают новой базовой роли: WAREHOUSE создаёт документ, отдельный MANAGER подтверждает и оценивает. Для существующего документа и точного replay используется его серверное created_at, для утверждения возврата — более ранняя дата исходного прихода и акта. Для связанного прихода учитывается и более ранняя дата исходного заказа поставщику; проверяется также дата возвращённого при replay документа. Исторический cutoff блокирует обе смешанные очереди; прямой UUID не обходит запрет. Права на исходные закупочные цены дополнительно требуются для оценки и очереди приходов с котировками. Физические остатки, НДС и immutable ledger не меняются при 403. В UI при отказе очищаются выбранные документы, очередь и закупочная цена; действия должны быть заново авторизованы. PG17: test_pg_auto_document_access_http.py (полный переход правил и повторов на одной fixture); Playwright: warehouse-receiving.spec.ts и warehouse-supplier-returns.spec.ts. Без DDL, реальных данных и release permission.


### Ограничения инвентаризации, перемещений и сверки (#166/#168/#446)

В закрытом synthetic-контуре партии, ожидающие подсчёты, локальные и
межфилиальные перемещения, сводка и детализация сверки требуют базовой
роли и SUPPLIER_ORDER:READ. Для подсчёта — CREATE, для независимого
утверждения — CLOSE, для перемещения — EDIT (в каждом случае также WRITE).
Для переноса политика проверяется в обоих филиалах до чтения, мутации и
повтора ключа. DENY любой применимой MANAGER/WAREHOUSE роли имеет приоритет.
Сводки, текущие остатки, очереди и переносы опираются на всю историю:
при заданном READ cutoff они целиком запрещены (403), а не показывают
неполный остаток как полный. Отдельный PRICE_PURCHASE DENY не запрещает
физическую сверку без цен, но по-прежнему запрещает коммерческую детализацию.
Правило не выдаёт новую роль, не отменяет независимое утверждение,
проверку резерва, серийных ограничений или неизменяемость ledger.
При отказе мутации инвентаризации очищаются формы, результат и списки.
PG17/HTTPS: test_pg_auto_stock_operational_policy_http.py;
Playwright: warehouse-stocktake.spec.ts. Без DDL и production доступа (#119).


### Политика выдачи, возврата и серийного учёта (#166/#168/#446)

Резерв, выдача, возврат из партии и серийные движения требуют
WAREHOUSE и SUPPLIER_ORDER READ/EDIT (+WRITE); регистрация серии — READ/CREATE
(+WRITE). Полный физический остаток опирается на всю историю, поэтому
cutoff запрещает такие мутации, включая replay. Серийный перенос проверяет
обе ветки, до выдачи ранее сохранённого результата или конфликта ключа.
Механик подтверждает выдачу без закупочной цены: MECHANIC плюс
INTAKE_ACT READ (все применимые роли) и WRITE механика с исторической датой
наряда. При отказе 403 проводок нет; при восстановлении прав повтор
с прежним ключом сохраняет идентичность результата. UI очищает данные при
отказе, сохраняет ранее неопределённый ключ для сверки/повтора и не показывает
результат другой ветки. PG17/HTTPS: test_pg_auto_stock_order_serial_policy_http.py.
Playwright: warehouse-work-orders.spec.ts, warehouse-serial-movements.spec.ts.
Миграций и расширения production нет.


**Завершение операционного складского ACL (#446):** внутренний приход требует SUPPLIER_ORDER READ/CREATE/WRITE, размещение и резерв — READ/EDIT/WRITE, внутреннее списание/возврат и закрытие актов — READ/CLOSE/WRITE. Создание актов пересортицы и списания — READ/CREATE/WRITE, независимое решение (включая отмену) — READ/CLOSE/WRITE; исходные WAREHOUSE/MANAGER и maker-checker обязательны. Список актов доступен с READ без CREATE/CLOSE, но сервер скрывает запрещённые действия и список партий для создания. ABC/XYZ JSON/CSV без цен проверяет полный READ, как и физическая сверка. Cutoff блокирует целиком, поскольку полнота исходных данных не гарантирована. При отказе мутации пересортицы UI очищает идентификаторы/результат и оставляет старый ключ ранее неопределённой операции. PG17: test_pg_auto_stock_remaining_policy_http.py. Без изменения DDL, legal/release gate #119.

**Оставшиеся низкоуровневые пути (#446):** возврат по SKU без партии также проверяет SUPPLIER_ORDER READ/EDIT/WRITE и отсутствие cutoff до записи/повтора. Для происхождения серии нужны READ/PRICE_PURCHASE и CREATE/WRITE кладовщика либо CLOSE/WRITE управляющего; повтор утверждения/заявления проверяет исходную область, даже если тот же сотрудник работает в двух организациях. PG17 регрессии: test_aggregate_return_policy_deny_cutoff_and_exact_replay, test_serial_provenance_replay_requires_create_close_and_original_branch. Действующая роль и maker-checker остаются обязательными.


### Конкурирующие возвраты по одному приходу (#168)

Новый черновик возврата не может превышать ещё не возвращённое количество исходного приходного документа, даже когда другая приёмка увеличила физический остаток той же партии. Закрытый HTTPS service проверяет остаток лимита под той же SKU-блокировкой, что и существующее подтверждение; точный replay старого черновика сохраняет исходный UUID после повторной проверки ACL. Незавершённые черновики не резервируют количество: при конкурирующем подтверждении SQL guard по-прежнему атомарно отклоняет лишний возврат (409), включая физические проводки и атрибуцию НДС. PG17/HTTPS `test_competing_return_documents_cannot_spend_another_receipt_quantity_or_vat` проверяет два конкурирующих акта, второй приход в ту же партию, точный replay, исчерпание количества и независимую стоимость/НДС другого прихода. Отклонённый ранее черновик сохраняется в аудите; отдельного отменяющего события для него нет. Это synthetic service guard, не новая production DB policy или юридический credit note. Миграций нет; rollback — revert, ранее проведённые движения неизменны.


## Терминальные решения по возвратам поставщику (#168)

Вместо удаления старого акта миграция `0145_auto_return_terminal` хранит одно неизменяемое решение APPROVE/CANCEL/REJECT. Исторические утверждения переносятся с исходными UUID, временем и actor; новый approval атомарно создаёт терминальную запись. Уникальность document_id исключает конкурирующие решения, включая прямую SQL-вставку под synthetic DB owner: конфликт approval откатывает также физические движения. WAREHOUSE может отменить только собственный открытый акт; другой MANAGER — отклонить. Требуются живые session, scope org/location, SUPPLIER_ORDER READ/CLOSE, исторический cutoff, CSRF и UUID Idempotency-Key. Точный повтор с тем же действием/причиной возвращает исходный UUID после повторной проверки прав. Отмена/отказ не меняют SKU, партии, net и VAT.

Закрытый `POST .../warehouse/supplier-returns/decisions` принимает `{locationId,documentId,action,note}` (CANCEL или REJECT), возвращает 201 `decisionId`, 403 при отсутствии прав, 409 при конфликте, 422 при неверном теле. GET `.../pending?view=own` — собственные открытые акты WAREHOUSE; default review — чужие открытые акты MANAGER. Оба списка исключают все терминальные акты. Сверка отличает CANCELED/REJECTED от DRAFT; не включает их в pendingReturnCount, не приписывает им оценку или выбытие. UI `/staff/warehouse-supplier-returns` поддерживает recovery собственного черновика и безопасный повтор решения.

PG17/HTTPS: `tests/integration/test_pg_auto_stock_supplier_return_http.py`; Playwright: `apps/web/e2e/warehouse-supplier-returns.spec.ts`, `apps/web/e2e/warehouse-reconciliation.spec.ts`. Downgrade блокируется при наличии отмен/отказов, чтобы не потерять историю. Это не production DB least-privilege enforcement, юридический возврат денег, кредит-нота или разрешение #119. До фактического CI/review на точном SHA пакет не считать принятым.

Частный OpenAPI: [auto-supplier-return-dev-openapi.json](../../contracts/auto-supplier-return-dev-openapi.json), четыре закрытых маршрута и DTO коммерческой сверки; проверка `tests/contract/test_auto_supplier_return_contract.py`. Не публикуется в production OpenAPI.

## Серийное выбытие по актам (#168, synthetic dev)

Черновики списания и возврата поставщику принимают необязательный `serialIds` (до 50 уникальных UUID; непустой список покрывает всё количество акта). Список записывается неизменяемо, не резервирует остаток. Серии должны находиться в исходной партии IN_STOCK; возврат поставщику дополнительно требует независимого подтверждения происхождения каждой серии именно из исходного оценённого прихода. Утверждение повторяет проверку под SKU lock и атомарно связывает каждую серию с физическим disposition и независимым решением; состояния WRITTEN_OFF/SUPPLIER_RETURNED терминальны. Отмена/отклонение не создают проводок или терминальных серий. Повтор не меняет список; competing approvals дают один успех. Без списка сохраняется прежний количественный режим, который не может уменьшить размещение ниже числа остающихся зарегистрированных серий. Никаких новых правил бухгалтерской себестоимости/НДС: прежняя атрибуция исходного прихода не изменена. Список UUID виден в журнале акта и коммерческой сверке с прежним ACL. Миграция 0147 аддитивна, downgrade блокируется при наличии серийных актов/истории. Реальные данные и production ACL остаются запрещены до #119.

Частные OpenAPI: [списание](../../contracts/auto-writeoff-dev-openapi.json), [возврат поставщику](../../contracts/auto-supplier-return-dev-openapi.json). Проверяются реальные DTO журнала/коммерческой сверки и схемы; поле serialIds не передаётся при утверждении, чтобы проверяющий не мог подменить состав акта.

Частный контракт создания/списка закупочных заявок, частичного прихода и отмены остатка: [supplier orders OpenAPI](../../contracts/auto-supplier-order-dev-openapi.json), [процесс закупки](SUPPLIER_PURCHASES.md). Новые сравнения только в RUB; точный исторический replay неизменяем. API/PG17 contract tests не заменяют внешние supplier/legal/security gates.


**#115/#168:** карточка supplier order объединяет исходный дефицит, сравнение и документы прихода с раздельным статусом приёмки/оценки; переход к существующему maker/checker экрану, цена не передаётся URL. [Порядок работы](SUPPLIER_PURCHASES.md). Производственный и юридический scope остаётся blocked.
