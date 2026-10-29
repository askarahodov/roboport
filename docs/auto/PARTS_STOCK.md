# Автомобильный склад — синтетическое ядро PG17

**Статус:** current-dev для внутренней схемы и функции, production/HTTP/UI — **planned**. Родительские #115 и #168 остаются открытыми.

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

Миграция `0064_auto_receipt_document` создаёт immutable `auto_stock_receipt_document` (партия, филиал, количество, номер документа поставщика, автор) и `auto_stock_receipt_acceptance` (другой утверждающий, парные stock/lot events). Внутренний сервис `create_receipt_document` требует текущую роль WAREHOUSE; `accept_receipt_document` — текущую MANAGER того же филиала и иного пользователя. На этапе draft **остатки не меняются**; единственное подтверждение атомарно записывает физический `RECEIPT` и партийный `ALLOCATE`. Повторы запроса UUID безопасны лишь при активной сессии и наличии прав. Одинаковый пользователь с обеими ролями не может принять собственный документ. Аудит неизменяем, PG17 deferred constraint проверяет связанность проводок; rollback не оставляет частичных приходов. Тесты: `tests/integration/test_pg_auto_stock_receipt_document.py` (scope, actor, revoke, concurrency, rollback). Только synthetic current-dev; транспортный приход, цены/НДС, лицензированный заказ поставщику, production DB ACL, внешний HTTP/UI и полный документный workflow ещё planned. Не является закрытием #168.
