# Реестр доказательств текущих автомобильных операций

**Дата сверки:** 03.11.2026 · **Категория:** source-backed
`current-dev`; не юридическое разрешение на эксплуатацию.
Данные этого документа относятся к конкретным Python/React контрактам и
тестам `main` после PR #371. Проверка GitHub CI — автоматическая;
независимая ручная/юридическая/security-приёмка отдельно по #76/#119.

## Стандарты доказательства «работает»

Для каждого процесса ниже указаны: роль, действие, экран, сервер, хранилище
или состояние, проверка и **отрицательное поведение**. Маршрут существует
в React — **не означает**, что сервер разрешает действие. Для production
необходимо доказать полномочия и корректный клиентский consent, безопасные
миграции, backup/restore, аудит и реальную приёмку.

| ID | Given / When / Then (dev) | Источник UI → server / SQL | Проверка и отказы | Статус |
|---|---|---|---|---|
| R01 | **Given** ADMIN организации; **When** открывает журнал; **Then** видит только её назначения/отзывы | `AutoStaffAdmin.tsx` → `auto_org_staff_http.py`, `auto_organization_role_grant` | `auto-staff-console.spec.ts`; `test_pg_auto_org_staff_http.py`; 403 чужая организация | current-dev |
| R02 | **Given** INTAKE и клиент; **When** предъявлен код и независимый MANAGER одобрил связь; **Then** только verified link даёт доступ | `StaffAutoClaims.tsx`, `CustomerAutoClaim.tsx` → `auto_vehicle_claim_http.py`, `auto_vehicle_customer_link` | `auto-staff-review.spec.ts`; `test_pg_auto_vehicle_claim_http.py`; 403/429, отзыв | current-dev |
| R03 | **Given** доступная бронь и филиал; **When** INTAKE создаёт/переносит/отменяет запись; **Then** действующая бронь не двойная | `AutoCalendar.tsx` → `auto_appointment_http.py`, `auto_bay_booking`, `auto_appointment` | `auto-calendar.spec.ts`; SQL конкурентная бронь; 409 | current-dev |
| R04 | **Given** verified WorkOrder; **When** INTAKE выбирает услуги; **Then** состав фиксируется до публикации сметы | `AutoOrderServices.tsx` → `auto_order_service_http.py`, `auto_work_order_service` | `auto-order-services.spec.ts`; 403 чужая связь, 409 после публикации | current-dev |
| R05 | **Given** INTAKE; **When** фиксирует пробег и осмотр; **Then** осмотр immutable | `AutoWorkshopQc.tsx` → `auto_workshop_qc_http.py`, `auto_work_order_inspection` | `auto-workshop-qc.spec.ts`; 409 при попытке изменить осмотр | current-dev |
| R06 | **Given** MANAGER и услуги; **When** публикует полную priced смету; **Then** версия immutable, total в копейках и SHA-256 | `AutoEstimateConsole.tsx` → `auto_work_order_estimate_http.py`, `auto_work_order_estimate_revision` | `auto-estimates.spec.ts`; `test_pg_auto_work_order_estimate_http.py`; 409 stale version | current-dev |
| R07 | **Given** CLIENT исходного заказчика; **When** читает актуальную версию и выбирает APPROVED/DECLINED; **Then** фиксируется одно решение | `CustomerAutoOrders.tsx` → `auto_customer_orders_http.py` + `auto_work_order_estimate_http.py`, `auto_work_order_estimate_decision` | `customer-auto-orders.spec.ts`; `test_pg_auto_customer_orders_http.py`; новый владелец не наследует историю | current-dev |
| R08 | **Given** APPROVED и осмотр; **When** MANAGER запускает наряд; **Then** только MECHANIC фиксирует труд, WAREHOUSE выдаёт партии | `AutoWorkExecution.tsx` → `auto_work_order_execution_http.py`, `auto_work_order_labor_entry`, stock lot ledger | `auto-work-execution.spec.ts`; `test_pg_auto_work_order_execution_http.py`; 403/409/конкурентность | current-dev |
| R09 | **Given** труд по всем услугам и принятые детали; **When** механик завершает работы; **Then** другой MECHANIC проводит QC после finish | `AutoWorkshopQc.tsx` → `auto_workshop_qc_http.py`, `auto_work_order_qc_event` | `auto-workshop-qc.spec.ts`; SQL guard independence, QC до finish = отказ | current-dev |
| R10 | **Given** WAREHOUSE; **When** подсчитал партию; **Then** другой MANAGER подтверждает изменение физического и партийного остатков | `WarehouseStocktake.tsx` → `auto_stocktake_http.py`, stock ledger | `warehouse-stocktake.spec.ts`; 403 собственное утверждение, 409 изменение партии | current-dev |
| R11 | **Given** зарезервированная партия; **When** WAREHOUSE выдаёт/MECHANIC подтверждает получение; **Then** остатки атомарны, handoff append-only | `WarehouseWorkOrders.tsx` → stock issue/handoff services, `auto_stock_mechanic_handoff` | `warehouse-work-orders.spec.ts`; `test_pg_auto_work_order_execution_http.py`; 409 нехватка | current-dev |
| R12 | **Given** оформлена партия; **When** WAREHOUSE готовит приход; **Then** независимый MANAGER подтверждает изменение остатков | `WarehouseReceiving.tsx`, stock receipt services | `warehouse-receiving.spec.ts`; контроль на чужой филиал и повтор | current-dev |
| R13 | **Given** QC_PASS, нулевые synthetic due и резервы; **When** MANAGER запечатывает preview; **Then** появляется один SHA-256 и `releaseAllowed=false` | `AutoDemoClose.tsx` → `auto_demo_close_http.py`, `auto_order_demo_handover_preview` | `auto-demo-close.spec.ts`; `test_pg_auto_demo_close_http.py`; двойной платёж/возврат/403 | current-dev |
| R14 | **Given** активная CLIENT с исходной customer link; **When** запрашивает историю; **Then** видит только свои заказы, сметы, QC и preview | `CustomerAutoOrders.tsx` → `auto_customer_orders_http.py`, original appointment customer_link_id | `customer-auto-orders.spec.ts`; `test_pg_auto_customer_orders_http.py`; 404 чужой заказ, revoke | current-dev |

## Главные межролевые инварианты

**Разделение обязанностей.** Приёмочный осмотр и QC имеют разных
ответственных; работавший над нарядом MECHANIC не вправе сам себе
подтвердить QC; складской подсчёт и его утверждение не выполняются одним
исполнителем. Не выводите «можно» из двух ролей одного пользователя.

**Точная связь с заказчиком.** Действующая и verified
`auto_vehicle_customer_link` связана с исходной
`auto_appointment.customer_link_id`; смена владельца автомобиля
**не переносит** предыдущие заказ-наряды, платежи или документы по одному
VIN. Недействующие customer/account link и revoked role закрывают доступ.

**Идемпотентность и история.** Сметы, решения клиента, QC, труд, stock ledger
и demo preview не переписываются. При неизвестном результате безопасен
только прежний `requestKey`; избыточные/конкурентные действия отвергаются
сервером. В HTTP должны оставаться `no-store`, CSRF/origin, 403/404
anti-BOLA и 409 на конфликт версии/остатка.

**Эксплуатация.** Ни один `current-dev` путь не может обслуживать
реальные VIN/ПДн, деньги, лицензированные OEM-каталоги или физическую
выдачу. `SIM_PAYMENT`/`SIM_REFUND` — не денежные проводки; пакет
`SYNTHETIC_PRE_HANDOVER_PREVIEW` — не акт передачи.

## Gap-реестр: не закрывать задачи без подтверждений

| Незавершённая область | Issue | Что потребует независимого доказательства |
|---|---|---|
| Бизнес/маркет benchmark и приоритеты | #108 | Подтверждение владельца продукта, независимый business review |
| Production бренд и целевой auto UI | #110 | Права на бренд, доступность 390/1280, нулевая robot-лексика |
| Полный WorkOrder и документы | #114, #167 | Нормочасы, диагностика/фото, юридический акт и выдача |
| Склад OEM/коммерческие поставщики | #111, #115, #168 | Лицензия, применяемость, себестоимость, независимый складской QA |
| Реальные деньги/ККТ/эквайринг | #116, #170 | Фискальные/legal gates, sandbox эквайринга и возвратов |
| CRM и аналитика | #169, #171 | Достоверные события и первичные документы, ABAC отчёты |
| Реальный перенос legacy данных | #172 | Согласия, provenance, backup/restore и reversible cutover |
| Физическое удаление robot domain | #165 | Матрица URL/API/UI, cutover, откат, legal + security |
| Мобильный интерфейс и уведомления | #118 | iOS/Android auth, privacy/security, offline и mobile QA |

[Быстрый старт](../guides/QUICK_START.md) ·
[Ролевая матрица](../guides/ACCESS_AND_TEST_MATRIX.md) ·
[Чек-лист QA и снимки](../guides/SCREENSHOT_EVIDENCE.md) ·
[Архив legacy](LEGACY_ARCHIVE.md).

**Критерий публикации:** для каждой новой фичи в одном PR должны быть
актуализированы роль, экран, API/SQL-триггер, отрицательные тесты и
руководство. Отсутствие замечаний ревью **не равно** независимому
бизнес/security/юридическому одобрению.


## Точность SHA для release preflight (#119, current-dev)

Автоматическая проверка выпуска теперь сверяет указанный SHA успешного `main` CI с **живым** `refs/heads/main` дважды — до и после проверки GitHub run/jobs и обязательных задач. Успех исторического CI после нового push в `main` отклоняется как `STALE_MAIN_SHA`; изменение HEAD во время проверки — `MAIN_MOVED_DURING_PREFLIGHT`; некорректный ref — `MAIN_REF_NOT_VERIFIED`. Набор блокирующих GitHub issues охватывает полный автомобильный контур: права на бренд/каталог, запись, склад, финансы, приватность, cutover, роли и release gate. Проверка выполняется в `scripts/check_auto_release_preflight.py`, негативные контрактные тесты `tests/contract/test_auto_release_preflight.py` входят в `api` CI. **Даже если все технические проверки прошли, output остаётся `verdict=BLOCKED` и `productionApproved=false`**, так как реальную подлинность согласий и право на выпуск подтверждает только независимое ответственное решение. [Подробный чек-лист и команды](RELEASE_PREFLIGHT.md).
