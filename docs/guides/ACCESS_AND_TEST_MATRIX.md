# Матрица ролей, экранов и тестов — автомобильный dev

**Сверено с кодом:** 03.11.2026, ветка `main` после клиентского PR #371.
**Готовность:** только current-dev на синтетическом HTTPS loopback. Все
операции доступны **при действующих правах на конкретные организацию,
филиал и объект**, а не по одной видимости элемента меню. Роль бухгалтера
или клиента не наследует полномочия MANAGER/WAREHOUSE.

**Обновление 10.11.2026:** действующая помощь доступна из `/staff` по [карте операций](STAFF_TASKS.md), соответствие навигации и ссылок проверяется `apps/web/e2e/staff-access.spec.ts`. Матрица ниже описывает backend-ограничения; визуальная доступность раздела не равна tenant/role/object grant. Ручная независимая QA #76 отложена, не PASS.

## Рабочие операции: роль → путь → проверка

| Действие | Роли и границы | Рабочий URL (dev) | React UI | E2E-сценарий | Руководство |
|---|---|---|---|---|---|
| Журнал назначений и отзывов | ADMIN только своей организации, read-only | `/staff/auto-admin` | `AutoStaffAdmin.tsx` | `auto-staff-console.spec.ts` | [Администратор](administrator.md) |
| Начать привязку и независимое решение | INTAKE и отдельно MANAGER своей организации | `/staff` («Автомобили (dev)») | `StaffAutoClaims.tsx` | `auto-staff-review.spec.ts` | [Приёмщик](intake.md), [Руководитель](manager.md) |
| Записи и рабочие посты | INTAKE, MANAGER своего филиала | `/staff/auto-calendar` | `AutoCalendar.tsx` | `auto-calendar.spec.ts` | [Приёмщик](intake.md) |
| Выбор услуг наряда | INTAKE своей организации/филиала; live proof | `/staff/auto-order-services` | `AutoOrderServices.tsx` | `auto-order-services.spec.ts` | [Приёмщик](intake.md) |
| Приёмочный осмотр | INTAKE, конкретный наряд | `/staff/auto-workshop-qc` | `AutoWorkshopQc.tsx` | `auto-workshop-qc.spec.ts` | [Приёмщик](intake.md) |
| Независимая QC-проверка | MECHANIC, **не исполнитель** этой работы | `/staff/auto-workshop-qc` | `AutoWorkshopQc.tsx` | `auto-workshop-qc.spec.ts` | [Механик](mechanic.md) |
| Новая версия сметы | MANAGER филиала, до клиентского решения | `/staff/auto-estimates` | `AutoEstimateConsole.tsx` | `auto-estimates.spec.ts` | [Руководитель](manager.md) |
| Запуск исполнения | MANAGER при APPROVED и осмотре | `/staff/auto-execution` | `AutoWorkExecution.tsx` | `auto-work-execution.spec.ts` | [Руководитель](manager.md) |
| Факт трудозатрат / получение деталей | MECHANIC, только согласованные работы/выдачи | `/staff/auto-execution` | `AutoWorkExecution.tsx` | `auto-work-execution.spec.ts` | [Механик](mechanic.md) |
| Выдача деталей из резерва | WAREHOUSE при IN_PROGRESS | `/staff/auto-execution` | `AutoWorkExecution.tsx` | `auto-work-execution.spec.ts` | [Склад](warehouse.md) |
| Инвентаризация партий | WAREHOUSE подсчитывает; другой MANAGER утверждает | `/staff/warehouse-stocktake` | `WarehouseStocktake.tsx` | `warehouse-stocktake.spec.ts` | [Склад](warehouse.md) |
| Приход партии | WAREHOUSE черновик, независимый MANAGER утверждает | `/staff/warehouse-receiving` | `WarehouseReceiving.tsx` | `warehouse-receiving.spec.ts` | [Склад](warehouse.md) |
| Выдача/возврат по наряду | WAREHOUSE, действующие резервы | `/staff/warehouse-work-orders` | `WarehouseWorkOrders.tsx` | `warehouse-work-orders.spec.ts` | [Склад](warehouse.md) |
| Пересортица | WAREHOUSE, согласование по правилам dev | `/staff/warehouse-reclassification` | `WarehouseReclassification.tsx` | `warehouse-reclassification.spec.ts` | [Склад](warehouse.md) |
| Возврат поставщику | WAREHOUSE, независимое утверждение | `/staff/warehouse-supplier-returns` | `WarehouseSupplierReturns.tsx` | `warehouse-supplier-returns.spec.ts` | [Склад](warehouse.md) |
| Серийные перемещения | WAREHOUSE в пределах доступного филиала | `/staff/warehouse-serial-movements` | `WarehouseSerialMovements.tsx` | `warehouse-serial-movements.spec.ts` | [Склад](warehouse.md) |
| Условная сверка по заказу | MANAGER; **не реальная касса** | `/staff/auto-demo-close` | `AutoDemoClose.tsx` | `auto-demo-close.spec.ts` | [Руководитель](manager.md) |
| Освобождение остатка резерва | WAREHOUSE, до immutable preview | `/staff/auto-demo-close` | `AutoDemoClose.tsx` | `auto-demo-close.spec.ts` | [Склад](warehouse.md) |
| Просмотр подтверждённых авто и предъявление кода | CLIENT, только свои подтверждения | `/account/vehicles` | `CustomerVehicles.tsx` | `auto-vehicles.spec.ts` | [Клиент](client.md) |
| Самостоятельная запись, перенос и отмена | CLIENT с подтверждённой собственной связью, до начала работ | `/account/auto-booking` | `CustomerAutoBooking.tsx` | `customer-auto-booking.spec.ts` | [Клиент](client.md), [Процесс](../auto/CUSTOMER_BOOKING.md) |
| История собственных заказов, QC, сметы и документ | CLIENT, **исходный customer-link на момент заказа** | `/account/auto-orders` | `CustomerAutoOrders.tsx` | `customer-auto-orders.spec.ts` | [Клиент](client.md) |
| Решение по последней версии сметы | CLIENT исходного заказчика, exact revision | `/account/auto-orders` | `CustomerAutoOrders.tsx` | `customer-auto-orders.spec.ts` | [Клиент](client.md) |
| Просмотр технического пакета | CLIENT исходного заказчика, без подписи | `/account/auto-demo-close` | `AutoDemoClose.tsx` | `auto-demo-close.spec.ts` | [Клиент](client.md) |

Это **проверяемый каталог**, а не заявка, что все роли управляются через
production-UI. В тестовом интерфейсе есть nav-ссылки на staff-пути, но сервер
на каждом запросе ещё раз проверяет grants/сессию/конкретный объект.
Для реального клиента и склада этот экран **не открыт**.

## Отдельные технические серверные границы

| Проверяемая функция | Серверный модуль | PG17 / contract доказательства |
|---|---|---|
| Ролевое назначение/отзыв | `auto_org_staff_http.py` | `test_pg_auto_org_staff_http.py` |
| Подтверждение клиент–автомобиль | `auto_vehicle_claim_http.py` | `test_pg_auto_vehicle_claim_http.py` |
| Запись, перенос/отмена | `auto_appointment_http.py` | `test_pg_auto_appointment_http.py` |
| Точный доступ к историческому заказу | `auto_customer_orders_http.py` | `test_pg_auto_customer_orders_http.py` |
| Неизменяемая смета | `auto_work_order_estimate_http.py` | `test_pg_auto_work_order_estimate_http.py` |
| Исполнение и складской handoff | `auto_work_order_execution_http.py` | `test_pg_auto_work_order_execution_http.py` |
| Осмотр/QC | `auto_workshop_qc_http.py` | `test_pg_auto_workshop_qc_http.py` |
| Условная сверка и preview | `auto_demo_close_http.py` | `test_pg_auto_demo_close_http.py` |

Реестр связанных файлов и проверяемых ограничений — в
[доказательствах операций](../auto/OPERATIONS_EVIDENCE.md).

## Не готово (planned / blocked)

| Целевая возможность | Почему ещё нет | Блокеры |
|---|---|---|
| Настоящие платежи, кассы, возврат денег, 54-ФЗ/эквайринг | `SIM_PAYMENT` не является оплатой | #170, #119 |
| Выпуск и юридическое подписание акта/выдача автомобиля | Preview dev `releaseAllowed: false` | #114, #119 |
| Публичный автомобильный веб-сайт с подтверждённым брендом | Нет brand/legal approval и decommission | #110, #165, #119 |
| Production самостоятельная запись и приём настоящих документов | В dev есть только синтетический booking, нет реального consent/identity/production ingress | #113, #117, #119, #124 |
| iOS/Android, offline/push | Mobile discovery, нет приложений | #118 |
| Коммерческие OEM-провайдеры и применяемость | Лицензия, внешние поставщики не разрешены | #111, #115 |
| Зарплаты, прибыль и ABC/XYZ | Нет достоверного финансового реестра | #171 |
| Реальный импорт истории клиентов и остатков | Пройден только synthetic dry-run | #172 |
| Вывод robot-URL и legacy-домена | Нужны backup/restore, cutover, legal/security gate | #165, #119 |

## Проверка каждой роли и версии

Для каждой current-dev операции приведите синтетический сценарий
**Given** действующая сессия и grant нужного филиала,
**When** выполнено действие с валидными входными данными,
**Then** появляется неизменяемая запись/чёткий статус без утечки PII.
Отдельно проверьте **401/403/404/409**, чужую организацию, отзыв прав,
дублирование ключа и конкурентное изменение, где это применимо.

Результаты CI сверяются по Git SHA и workflow run, а не только по зелёному
значку открытой ветки. Точный чек-лист ручного ролевого тестирования и
скриншоты — в [SCREENSHOT_EVIDENCE](SCREENSHOT_EVIDENCE.md);
независимый QA/авторизация релиза по #76/#119 ещё требуются.


Отдельный customer booking HTTP и негативные PG17 проверки: `auto_customer_booking_http.py`, `test_pg_auto_customer_booking_http.py`; они не входят в публичный OpenAPI до #119.
