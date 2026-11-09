# AutoDealer Online: постраничный GAP-аудит и план полного функционального соответствия

**Дата проверки:** 06.11.2026. **Владелец аудита:** [#108](https://github.com/askarahodov/roboport/issues/108). **Эпик продукта:** [#107](https://github.com/askarahodov/roboport/issues/107). **Машиночитаемый первоисточник нашего аудита:** [AUTODEALER_ONLINE_PAGES.json](AUTODEALER_ONLINE_PAGES.json).

**Принципиальное ограничение:** это **неполный проверенный срез**, а не инвентаризация абсолютно всех страниц дерева помощи. Внешний сайт может менять структуру; вложенные инструкции, которые не были подтверждены поиском/загрузкой, сюда не добавлялись. **#108 остаётся OPEN** до полного обхода, независимого business+architecture review и подтверждения владельцем продукта. Обзор «О программе» никогда не заменяет страницу конкретной операции.

## Что точно проверено

На дату фиксации подтверждены **30 страниц** в **12 тематических разделах**: 20 с частичным dev-покрытием (`D_PARTIAL`) и 10 без подтверждённого полного сценария (`PLANNED`). Никакая строка не называется `production` или `complete`. Статус оценивает **весь сценарий статьи**, а не наличие одноимённого модуля/кнопки. Проверка источника: `page` — публичный текст удалось прочитать; `search_excerpt` — факт страницы и содержание подтверждены поисковой выдачей сайта, **нужна повторная полная проверка**. Исходный сайт допускает кешированные статьи и встречающиеся отметки «Старый текст»: точная воспроизводимость на дату публикации требует отдельного ревью.

**Наши критерии:** `M1` — основной ремонтный/учётный контур, `M2` — расширенное управление/закупки, `M3` — мобильные и специализированные функции. Под `D_PARTIAL` понимаются только закрытые synthetic dev-интерфейсы, ограниченные тестовыми данными; legacy robot APIs не засчитываются. Контракт полноты и честных статусов: `python scripts/check_autodealer_audit.py` — offline CI, **не** crawler удалённого сайта.

| ID | Раздел | Страница | Матрица | Приоритет | Наш статус | Issue | Не закрыто |
|---|---|---|---|---|---|---|---|
| ADP-001 | Обзор | [О программе](https://autodealer.ru/online/help/o-programme.html) | A04 | M1 | D_PARTIAL | [#107](https://github.com/askarahodov/roboport/issues/107) | Полный контур предприятия, интеграции и production еще не готовы |
| ADP-002 | Ремонт | [Заказ-наряды](https://autodealer.ru/online/help/zakaz-nariady/index.html) | B01 | M1 | D_PARTIAL | [#441](https://github.com/askarahodov/roboport/issues/441) | Нет документного POSTED/CLOSED, актов, реальных оплат и выдачи |
| ADP-003 | Ремонт | [Создание заказ-наряда](https://autodealer.ru/online/help/zakaz-nariady/sozdanie-zakaz-nariada/index.html) | B01 | M1 | D_PARTIAL | [#441](https://github.com/askarahodov/roboport/issues/441) | Существующие экраны не образуют подписанный, проведенный, закрытый документ |
| ADP-004 | Ремонт | [Заказ-наряд. Работы](https://autodealer.ru/online/help/zakaz-nariady/sozdanie-zakaz-nariada/zakaz-nariad.-raboty.html) | B05 | M1 | D_PARTIAL | [#440](https://github.com/askarahodov/roboport/issues/440) | Нет комплексов, исполнителей, кратности, лимитов скидок |
| ADP-005 | Ремонт | [Заказ-наряд. Товары](https://autodealer.ru/online/help/zakaz-nariady/sozdanie-zakaz-nariada/zakaz-nariad.-tovary.html) | C05 | M1 | D_PARTIAL | [#443](https://github.com/askarahodov/roboport/issues/443) | Нет полного состава материалов/товаров в UI и договорных документов |
| ADP-006 | Приёмка | [Заказ-наряд. Акт осмотра](https://autodealer.ru/online/help/zakaz-nariady/sozdanie-zakaz-nariada/zakaz-nariad.-akt-osmotra.html) | B02 | M1 | D_PARTIAL | [#439](https://github.com/askarahodov/roboport/issues/439) | Чек-лист current-dev без фото зон/юридической подписи и версии документа |
| ADP-007 | Финансы | [Заказ-наряд. Платежи](https://autodealer.ru/online/help/zakaz-nariady/sozdanie-zakaz-nariada/zakaz-nariad.-platezhi.html) | D06 | M1 | PLANNED | [#445](https://github.com/askarahodov/roboport/issues/445) | Нет реального cashier/payments/refunds/фискального документа |
| ADP-008 | Запись | [Запись на ремонт](https://autodealer.ru/online/help/zapis-na-remont/index.html) | A07 | M1 | D_PARTIAL | [#113](https://github.com/askarahodov/roboport/issues/113) | Есть booking dev; нет полного продуктового поиска/поставок/настроек |
| ADP-009 | Запись | [Онлайн-запись на ремонт](https://autodealer.ru/online/help/zapis-na-remont/onlain-zapis-na-remont.html) | A08 | M2 | PLANNED | [#442](https://github.com/askarahodov/roboport/issues/442) | Нет публичного безопасного виджета, consent, mock-доставки и антиспама |
| ADP-010 | Мобильность | [Запись в мобильном приложении](https://autodealer.ru/online/help/zapis-na-remont/zapis-v-mobilnom-prilozhenii.html) | E03 | M3 | PLANNED | [#118](https://github.com/askarahodov/roboport/issues/118) | Нет отдельного native staff app с отзывом полномочий |
| ADP-011 | Приёмка | [Диагностический лист](https://autodealer.ru/online/help/diagnosticheskii-list-neispravnosti/index.html) | B03 | M1 | D_PARTIAL | [#439](https://github.com/askarahodov/roboport/issues/439) | Есть коды диагностик на услугу, нет дефектов по узлам и версий акта |
| ADP-012 | Ремонт | [Работы и комплексы](https://autodealer.ru/online/help/raboty-i-kompleksy/index.html) | B05 | M1 | D_PARTIAL | [#440](https://github.com/askarahodov/roboport/issues/440) | Есть selected services и norm-hour pricing, нет полного каталога комплексов |
| ADP-013 | CRM | [CRM](https://autodealer.ru/online/help/crm/index.html) | D01 | M2 | D_PARTIAL | [#444](https://github.com/askarahodov/roboport/issues/444) | Есть synthetic лиды, нет подтверждённого revenue lineage |
| ADP-014 | Клиенты | [Клиенты](https://autodealer.ru/online/help/klienty/index.html) | A01 | M1 | D_PARTIAL | [#112](https://github.com/askarahodov/roboport/issues/112) | Нет consent-complete customer master и реальных балансов |
| ADP-015 | Отчёты | [Отчёты](https://autodealer.ru/online/help/otchety-monitor-i-grafiki/otchety/index.html) | D10 | M2 | D_PARTIAL | [#448](https://github.com/askarahodov/roboport/issues/448) | Есть ограниченные агрегаты, нет доказуемой прибылей и полного экспорта |
| ADP-016 | Доступ | [Права доступа. Пользователи](https://autodealer.ru/online/help/prava-dostupa.-polzovateli.html) | A05 | M1 | D_PARTIAL | [#446](https://github.com/askarahodov/roboport/issues/446) | Есть scoped grants; нет per-document/per-price/period effective ACL |
| ADP-017 | Склад | [Номенклатура](https://autodealer.ru/online/help/nomenklatura/index.html) | C03 | M2 | D_PARTIAL | [#447](https://github.com/askarahodov/roboport/issues/447) | Нет bulk pricing, полноценного каталога аналогов и XLSX roundtrip |
| ADP-018 | CRM | [Воронка продаж](https://autodealer.ru/online/help/crm/voronka-prodazh.html) | D02 | M2 | D_PARTIAL | [#444](https://github.com/askarahodov/roboport/issues/444) | Нет verified payments и полной событийной conversion аналитики |
| ADP-019 | Кадры | [Начисление зарплаты](https://autodealer.ru/online/help/zarplata/nachislenie-zarplaty.html) | D09 | M3 | PLANNED | [#448](https://github.com/askarahodov/roboport/issues/448) | Нет payroll, юридического основания, формул и ролей |
| ADP-020 | Склад | [Создание номенклатуры](https://autodealer.ru/online/help/nomenklatura/sozdanie-nomenklatury.html) | C03 | M2 | D_PARTIAL | [#447](https://github.com/askarahodov/roboport/issues/447) | Нет полноценной price version и состава комплектов/файлов |
| ADP-021 | Кадры | [Расчет зарплаты](https://autodealer.ru/online/help/zarplata/raschet-zarplaty.html) | D09 | M3 | PLANNED | [#448](https://github.com/askarahodov/roboport/issues/448) | Synthetic labor actual_minutes не подтверждает payroll |
| ADP-022 | Финансы | [Движение денежных средств](https://autodealer.ru/online/help/dengi/dvizhenie-denezhnykh-sredstv.html) | D10 | M2 | PLANNED | [#445](https://github.com/askarahodov/roboport/issues/445) | Нет настоящего реестра денег и сверяемого кассового баланса |
| ADP-023 | Клиенты | [Объединение дубликатов](https://autodealer.ru/online/help/klienty/obedinenie-dublikatov.html) | A01 | M2 | PLANNED | [#112](https://github.com/askarahodov/roboport/issues/112) | Нет safe owner/document merge и консистентной privacy истории |
| ADP-024 | Склад | [Объединение дубликатов номенклатуры](https://autodealer.ru/online/help/nomenklatura/obedinenie-dublikatov.html) | C03 | M2 | PLANNED | [#447](https://github.com/askarahodov/roboport/issues/447) | Нет safe-merge с проверкой UOM, серий, SKU FK, rollback |
| ADP-025 | Склад | [Инвентаризация](https://autodealer.ru/online/help/skladskoi-uchet/inventarizatciia.html) | C08 | M2 | D_PARTIAL | [#168](https://github.com/askarahodov/roboport/issues/168) | Есть synthetic stocktake, нет утвержденного комиссионного документа |

| ADP-026 | CRM | [Канбан](https://autodealer.ru/online/help/crm/kanban.html) | D01 | M2 | D_PARTIAL | [#444](https://github.com/askarahodov/roboport/issues/444) | Нет конфигурируемого канбана и подтверждённого перехода к оплате |
| ADP-027 | Кадры | [Схемы начисления зарплаты](https://autodealer.ru/online/help/zarplata/skhemy-nachisleniia-zarplaty.html) | D09 | M3 | PLANNED | [#448](https://github.com/askarahodov/roboport/issues/448) | Нет версий оклада/тарифа и employee payroll |
| ADP-028 | Склад | [Импорт и экспорт номенклатуры](https://autodealer.ru/online/help/nomenklatura/import-i-eksport-nomenklatury.html) | E04 | M2 | D_PARTIAL | [#447](https://github.com/askarahodov/roboport/issues/447) | Нет защищённого массового XLSX UI и roundtrip |
| ADP-029 | Склад | [Заказ клиента и поставщику](https://autodealer.ru/online/help/skladskoi-uchet/protcenka/zakaz-klienta.-zakaz-postavshchiku.html) | C09 | M2 | PLANNED | [#443](https://github.com/askarahodov/roboport/issues/443) | Нет supplier PO → частичный приход → связь с WorkOrder |
| ADP-030 | Отчёты | [Отчёты по товарам](https://autodealer.ru/online/help/otchety-monitor-i-grafiki/otchety/otchety.-tovary.html) | D10 | M2 | D_PARTIAL | [#448](https://github.com/askarahodov/roboport/issues/448) | Нет item-level отчёта minimum/reserve/cost/drilldown |

## Главные полные вертикали, добавленные в backlog

Задачи созданы по **независимым производственным процессам**, а не по каждой кнопке. У всех есть исходная URL, бизнес-роли, Given/When/Then, PG17/E2E, негативные ACL/гонки, документация и обязательные юридические ограничения. Это **новые** уточняющие GAP-задачи под существующими эпиками; они не заменяют родительские #112–#119/#165–#173.

| Очерёдность | GitHub Issue | Срез функционала | До завершения |
|---|---|---|---|
| P1 / M1 | [#439](https://github.com/askarahodov/roboport/issues/439) | Полный акт осмотра: зоны, фото, версии, подписи | #112, #113, #114, #167, security/legal |
| P1 / M1 | [#440](https://github.com/askarahodov/roboport/issues/440) | Комплексы работ, исполнители, коэффициенты, скидки, дополнительное согласие | #114, #167, tenant ACL |
| P1 / M1 | [#441](https://github.com/askarahodov/roboport/issues/441) | Документный FSM ЗН, платежи/документы, закрытие и выдача | #114, #168, #170, независимый QC |
| P1–P2 | [#442](https://github.com/askarahodov/roboport/issues/442) | Публичный виджет записи с CRM и материалами | #113, #117, #169, consent |
| P2 / M2 | [#443](https://github.com/askarahodov/roboport/issues/443) | Заказы поставщикам с partial receipt и provenance | #115, #168, #170 |
| P2 / M2 | [#444](https://github.com/askarahodov/roboport/issues/444) | CRM от лида до подтверждённой оплаты и конверсии | #169, #170, #171 |
| P1–P2 | [#445](https://github.com/askarahodov/roboport/issues/445) | Деньги: касса, авансы, refunds, отчёт ДДС | #170, #171, gate #119 |
| P1 / M1 | [#446](https://github.com/askarahodov/roboport/issues/446) | Гранулярные document/price/discount/period grants | #166, #119 |
| P2 / M2 | [#447](https://github.com/askarahodov/roboport/issues/447) | Каталог SKU, версии цен, дубли, XLSX import/export | #168, #172 |
| P2–P3 | [#448](https://github.com/askarahodov/roboport/issues/448) | Управленческие отчёты, выработка и начисления | #171, #170 и legal/payroll |

## Доказательство M1: роль → экран → сервер → данные → тест → пробел

| Сценарий | Исполнитель | Есть сейчас (только synthetic) | Доказательство в коде и тестах | Недостающий end-to-end критерий |
|---|---|---|---|---|
| Авто и запись [A07] | INTAKE/CLIENT | `/staff/auto-calendar`, `/account/auto-booking` | `auto_appointment_service.py`, `auto_customer_booking.py`; PG17 customer booking, Playwright | Публичный widget + CRM consent и материалы [#442](https://github.com/askarahodov/roboport/issues/442) |
| Приёмочный акт [B02] | INTAKE | `/staff/auto-workshop-qc` | `auto_workshop_qc.py`, `test_pg_auto_workshop_qc_http.py` | Зоны, фото, версии, защищённые подписи [#439](https://github.com/askarahodov/roboport/issues/439) |
| Диагностика и работы [B03/B05] | MECHANIC/MANAGER | `/staff/auto-diagnostics`, `/staff/auto-estimates` | `auto_work_order_diagnostics.py`, `auto_work_order_estimate.py`, `test_pg_auto_norm_hour_estimate_http.py` | Комплексы, кратность, коэффициенты, max discount, исполнитель [#440](https://github.com/askarahodov/roboport/issues/440) |
| Согласие и выполнение [B06/B07] | CLIENT/MECHANIC | `/account/auto-estimates`, `/staff/auto-execution` | `auto_work_order_execution.py`, PG17 execution/QC, Playwright | Документный FSM, юридически согласованное закрытие/выдача [#441](https://github.com/askarahodov/roboport/issues/441) |
| Склад [C05/C08] | WAREHOUSE | Отдельные dev страницы stocktake и выдачи | `auto_stock_service.py`, `auto_stock_lot_work_order_issue.py` и PG17 tests | Supplier order→partial receipt→inventory reconciliation [#443](https://github.com/askarahodov/roboport/issues/443) |
| CRM [D01] | INTAKE/MANAGER | `/staff/auto-crm-leads` | `auto_crm_leads.py`, `auto_crm_funnel.py`, PG17 leads | Оплаченные сделки, канбан, согласия каналов [#444](https://github.com/askarahodov/roboport/issues/444) |
| Финансы и отчёты [D05/D10] | CASHIER/MANAGER | synthetic demo close и отчёты | `auto_demo_close.py`, `auto_demo_finance_report.py` | Реальные счета/оплаты/ККТ/первичка/ДДС [#445](https://github.com/askarahodov/roboport/issues/445) |
| Права [A05] | ADMIN | scoped grants | `auto_org_authority.py`, `auto_org_staff.py` | Document/price-level ACL и сроки истории [#446](https://github.com/askarahodov/roboport/issues/446) |

## Приёмка полного audit #108 и дальнейшая сверка

1. **Инвентаризация всех URL:** дополнить JSON из публичного дерева `/online/help/`, включая вложенные статьи и страницы, которые публикуются позднее; учитывать страницы, увиденные только через индексы поиска и ссылки на мобильную/desktop-справку — **не смешивать продукты**. На каждую добавленную страницу необходим отдельный URL, текст/источник, date checked, матрица, задача и конкретный gap.
2. **Соотнесение с кодом, а не названиями:** каждую строку M1 провести по actor→UI→API→PG17→тест→инструкция; проверить отзыв полномочий, BOLA, конкурентные изменения и idempotency; `legacy robot` не считается `current`.
3. **Полнота зависимостей:** отдельная приёмка лицензированных OEM/нормо-часов, касс/эквайринга/СБП/ЭДО, SMS/телефонии, кадровых/расчётных правил — без копирования частных каталогов и чужого UI.
4. **Независимый review:** владелец продукта/бизнес-аналитик и архитектор подтверждают, что нет ни одной недокументированной функции benchmark либо что функция явно обоснованно исключена; #108 не закрывается без этого протокола.
5. **Production:** gate [#119](https://github.com/askarahodov/roboport/issues/119) по security/privacy/backup/legal, а также полное удаление robot домена [#165](https://github.com/askarahodov/roboport/issues/165), до выпуска клиентского продукта.

**Авторские права:** только сравнительный перечень *бизнес-возможностей* и ссылки на публичные статьи, без копирования интерфейса, скриншотов, программного кода, текстов и закрытых нормативов AutoDealer. Реальные ПДн/платежи до gate #119 не используются.

## Результат дедупликации второго прохода (06.11.2026)

При дополнительной сверке пяти вложенных статей найдено пересечение постановок #450–#459 с уже имеющимися полными вертикалями #439–#448. **Не следует вести два конкурирующих PR по одной бизнес-функции.** Сохраняем первоначальные owner Issues: #439 (акт), #440 (работы/комплексы/нормы/исполнители), #441 (FSM/выдача), #442 (виджет), #443 (закупка), #444 (CRM), #445 (касса/cashflow), #448 (выработка/зарплата). Дубли закрываются с явными связями и уникальными подробностями в комментариях owner Issues, без фиктивного закрытия самих функций. Машиночитаемый список теперь содержит 30 Online страниц в 12 разделах; это **по-прежнему не полный crawl** и не подтверждённый бизнес-аналитиком 100% gap audit.

## Дополнительная сверка процесса работ в ЗН (#440, PR #463, current-dev)

Функциональная зона B04–B07: в ветке PR #463 реализованы immutable
комплексы филиальных услуг и материалов с синхронным резервом SKU,
раздельные `PART`/`CONSUMABLE`, факторные цены без float, предложение
и независимое утверждение тарифной версии/лимита скидки, живое назначение
MECHANIC по смене, независимый QC и отдельное клиентское согласие допработ.
Доказательства: `auto_work_complexes.py`,
`auto_work_order_estimate.py`, `auto_work_tariffs.py`,
`auto_work_order_additional.py`, миграции 0107–0111 и PG17/Playwright
тесты по соответствующим именам. Статус матрицы и родителей не меняем
до полного CI, code review и слияния с main. Лицензированные нормативы,
payroll, деньги, подпись и production допуск по #119 за пределами этих
synthetic результатов.
