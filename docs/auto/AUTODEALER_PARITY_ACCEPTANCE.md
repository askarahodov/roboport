# AutoDealer Online → «БоксРитм»: реестр функций и критерии полного соответствия

**Срез источников:** 19.11.2026 · **Владелец:** [#108](https://github.com/askarahodov/roboport/issues/108) / [#107](https://github.com/askarahodov/roboport/issues/107) · **Статус:** НЕ ГОТОВО К ПОЛНОМУ КЛОНИРОВАНИЮ ПО ФУНКЦИЯМ.

Этот документ — **собственная спецификация поведения**, созданная по публичным справочным статьям [AutoDealer Online](https://autodealer.ru/online/help/o-programme.html), а не копия их текста, схем БД, программного кода, товарных знаков, платных справочников, дизайна или скриншотов. Требуем **эквивалент бизнес-возможностей и пользовательских результатов**, но реализуем их независимо в собственном интерфейсе и нашей документации. Термин «клон» здесь **не означает** наличие лицензии на чужое ПО или доступ к платным базам OEM, телефонии, банкам, фискальным сервисам.

## Что доказано и что НЕ доказано

- Текущий реестр: **53** уникальные статьи, **16** тематических групп и **94** атомарных критерия приёмки.
- Статьи: **32** с частичным synthetic dev-покрытием по историческому аудиту; **21** с незакрытыми существенными функциями. Это **оценка статьи**, а не доказательство отдельных пунктов ниже.
- Все 94 критерия сейчас **`NOT_PROVEN_COMPLETE`**: наличие похожей функции или интеграционного теста не доказывает весь пользовательский сценарий, безопасность и применимость к production. `NOT_PROVEN_COMPLETE` **не утверждает**, что функция полностью отсутствует.
- Доступная справочная страница «О программе» — только обзор. Дерево навигации, вложенные статьи, новые обновления, региональные различия, мобильные приложения и внешние поставщики **не обойдены полностью**. Источник не удалось получить целым sitemap; **ни 53 статьи, ни 94 проверки не называются исчерпывающим набором**.
- `sourceEvidence=page` — мы открыли полный текст страницы (или исходный аудит это фиксировал на своей дате); `search_excerpt` — только индексированная выдержка, необходима полная повторная проверка перед приёмкой.
- Даты проверки статьи сохраняются отдельно: первые 30 описывают срез 06.11.2026, дополнительные 19 — 18.11.2026; шесть ранее индексированных статей повторно открыты полностью 19.11.2026, ещё четыре статьи обнаружены поиском 19.11.2026. На текущем срезе 30 статей имеют `page`, 23 — `search_excerpt`; новые ADP-050 и ADP-052 доступны полным кешированным текстом с отметкой «Старый текст», а ADP-051 и ADP-053 пока только по поисковой выдержке. Нельзя переписывать старые даты как будто страницы перепроверены. Offline CI не проверяет изменения удалённого сайта.

**Источники фактов:** [постраничный реестр](AUTODEALER_ONLINE_PAGES.json), [карта GAP и владельцев](AUTODEALER_PAGE_AUDIT.md), [укрупнённая матрица 52 возможностей](FUNCTIONAL_MATRIX.md), машинный [реестр критериев](AUTODEALER_FUNCTION_REQUIREMENTS.json), [архитектура нашего продукта](ARCHITECTURE.md), [дорожная карта](FEATURE_ROADMAP.md).

## Приёмка каждой функции (без «галочки за наличие кнопки»)

Функция считается **DEV_VERIFIED** только если одновременно доказаны: (1) локальный исходный сценарий AutoDealer и полный набор правил на дату версии; (2) работающие русские UI и авторизованный API; (3) надлежащая собственная модель данных и source-of-truth; (4) позитивные + отрицательные PG17/HTTP, сценарий двух организаций/филиалов, revoke, CSRF/IDOR, повтор и гонки; (5) экранный Playwright при затронутом UI; (6) русская ролевая инструкция с точными действиями и rollback; (7) code review и успешный целевой PR CI. Публичный OpenAPI включать для production endpoints, не для dev-only.

**PRODUCTION_VERIFIED** возможен только дополнительно после прав на источники данных, законной первичной документации, реальных провайдеров и согласий, независимых QA/security/legal/ops sign-off, миграции/restore и release gate [#119](https://github.com/askarahodov/roboport/issues/119). Сейчас таких доказательств нет.

Колонки ниже: **ADF** — атомарное требование, **ADP** — ссылка на исходную статью, **матрица** — наш coarse capability, **issue** — ответственность за незакрытую функцию. Вся таблица имеет статус `NOT_PROVEN_COMPLETE` до присоединения проверяемых test/document evidence.

## Документы (4 критериев)

| Критерий | Действие | Acceptance (что должно реально работать) | Источник | Матрица / Issue |
|---|---|---|---|---|
| ADF-031 | Единый список документов | Создавать, проводить, возвращать в черновик, копировать и связывать документы по строгим правам и статусам. | [ADP-031](https://autodealer.ru/online/help/obshchie-spravochniki/dokumenty/index.html) | B10 / [#441](https://github.com/askarahodov/roboport/issues/441) |
| ADF-072 | Копирование документа без платежей | Копия сохраняет разрешённые строки, но никогда не дублирует платёж, первичный чек и оригинальную подпись. | [ADP-031](https://autodealer.ru/online/help/obshchie-spravochniki/dokumenty/index.html) | B10 / [#441](https://github.com/askarahodov/roboport/issues/441) |
| ADF-073 | Закрытый период документов | Документ ранней даты нельзя редактировать или удалять, если период закрыт; исключение для платежа строго авторизовано. | [ADP-031](https://autodealer.ru/online/help/obshchie-spravochniki/dokumenty/index.html) | B10 / [#441](https://github.com/askarahodov/roboport/issues/441) |
| ADF-074 | Групповой счёт и счёт-фактура | Массовая операция для разных контрагентов отклоняется без частичных документов, все успешные связи проверяемы. | [ADP-031](https://autodealer.ru/online/help/obshchie-spravochniki/dokumenty/index.html) | B10 / [#441](https://github.com/askarahodov/roboport/issues/441) |

## Доступ (3 критериев)

| Критерий | Действие | Acceptance (что должно реально работать) | Источник | Матрица / Issue |
|---|---|---|---|---|
| ADF-016 | Права пользователей | Создать грант конкретной роли и проверить запрет по чужой компании, отзыв, denial и ограничение истории. | [ADP-016](https://autodealer.ru/online/help/prava-dostupa.-polzovateli.html) | A05 / [#446](https://github.com/askarahodov/roboport/issues/446) |
| ADF-035 | Пользователи и роли | Создать пользователя, выдать ограниченные права, проверить negative ACL на двух компаниях и моментальный отзыв. | [ADP-035](https://autodealer.ru/online/help/nachalo-raboty-s-sistemoi/dobavlenie-polzovatelei.html) | A05 / [#446](https://github.com/askarahodov/roboport/issues/446) |
| ADF-080 | Отзыв пользователя | При блокировке пользователя все ранее выданные сессии и токены немедленно перестают давать доступ. | [ADP-035](https://autodealer.ru/online/help/nachalo-raboty-s-sistemoi/dobavlenie-polzovatelei.html) | A05 / [#446](https://github.com/askarahodov/roboport/issues/446) |

## Запись (4 критериев)

| Критерий | Действие | Acceptance (что должно реально работать) | Источник | Матрица / Issue |
|---|---|---|---|---|
| ADF-008 | Запись в календаре | Создать запись на конкретный пост и время, затем перенести или отменить её без пересечения занятости. | [ADP-008](https://autodealer.ru/online/help/zapis-na-remont/index.html) | A07 / [#113](https://github.com/askarahodov/roboport/issues/113) |
| ADF-009 | Публичная онлайн-запись | Пройти запись внешнего клиента на доступный слот с законным consent, защитой от спама и созданием связанных данных без утечки контактов. | [ADP-009](https://autodealer.ru/online/help/zapis-na-remont/onlain-zapis-na-remont.html) | A08 / [#442](https://github.com/askarahodov/roboport/issues/442) |
| ADF-058 | Календарь по рабочим постам | Сменить представление дня/недели/постов и исключить двойное бронирование при двух одновременных запросах. | [ADP-008](https://autodealer.ru/online/help/zapis-na-remont/index.html) | A07 / [#113](https://github.com/askarahodov/roboport/issues/113) |
| ADF-059 | Согласие и антиспам виджета | Зафиксировать отдельное доказательство consent и rate limit для публичного посетителя; не передавать контакты в открытые журналы. | [ADP-009](https://autodealer.ru/online/help/zapis-na-remont/onlain-zapis-na-remont.html) | A08 / [#442](https://github.com/askarahodov/roboport/issues/442) |

## Интеграции (4 критериев)

| Критерий | Действие | Acceptance (что должно реально работать) | Источник | Матрица / Issue |
|---|---|---|---|---|
| ADF-041 | Чаты и сообщения | Получить и отправить разрешённое сообщение клиента через подключённого провайдера с opt-out, audit и повторной доставкой. | [ADP-041](https://autodealer.ru/online/help/integratcii/chaty/index.html) | D03 / [#169](https://github.com/askarahodov/roboport/issues/169) |
| ADF-043 | Онлайн-касса | Провести фискальную операцию через разрешённый провайдер и получить идентификатор/статус действительного чека. | [ADP-043](https://autodealer.ru/online/help/integratcii/kassy/kassovyi-modul.html) | D07 / [#170](https://github.com/askarahodov/roboport/issues/170) |
| ADF-084 | Мессенджер consent | Маркетинговые уведомления требуют отдельного разрешения, opt-out и ответа провайдера; служебные сообщения не подменяются рассылкой. | [ADP-041](https://autodealer.ru/online/help/integratcii/chaty/index.html) | D03 / [#169](https://github.com/askarahodov/roboport/issues/169) |
| ADF-085 | Чек онлайн-кассы и повтор платежа | Проверить подпись и idempotency внешней кассы; дубликат callback не создаёт второй чек или денежную проводку. | [ADP-043](https://autodealer.ru/online/help/integratcii/kassy/kassovyi-modul.html) | D07 / [#170](https://github.com/askarahodov/roboport/issues/170) |

## Кадры (7 критериев)

| Критерий | Действие | Acceptance (что должно реально работать) | Источник | Матрица / Issue |
|---|---|---|---|---|
| ADF-019 | Начисление зарплаты | Рассчитать начисление по исполнителю из документально подтверждённой работы и действовавшей версии тарифов. | [ADP-019](https://autodealer.ru/online/help/zarplata/nachislenie-zarplaty.html) | D09 / [#448](https://github.com/askarahodov/roboport/issues/448) |
| ADF-021 | Расчёт и выдача зарплаты | Показать начислено, выдано и задолженность на начало/конец периода и провести документ выплаты без двойного списания. | [ADP-021](https://autodealer.ru/online/help/zarplata/raschet-zarplaty.html) | D09 / [#448](https://github.com/askarahodov/roboport/issues/448) |
| ADF-027 | Схемы зарплаты | Настроить окладную, тарифную и KPI модель с датами действия и безопасной перерасчётной историей. | [ADP-027](https://autodealer.ru/online/help/zarplata/skhemy-nachisleniia-zarplaty.html) | D09 / [#448](https://github.com/askarahodov/roboport/issues/448) |
| ADF-034 | Сотрудники компании | Завести сотрудника с назначением компании и периодом работы; увольнение должно немедленно отозвать операции. | [ADP-034](https://autodealer.ru/online/help/sotrudniki.html) | A05 / [#446](https://github.com/askarahodov/roboport/issues/446) |
| ADF-036 | Табель рабочего времени | Настроить типы дней, заполнить план и факт смен, проверить роли и расчёт отработанных часов. | [ADP-036](https://autodealer.ru/online/help/zarplata/tabel-ucheta-rabochego-vremeni.html) | D09 / [#448](https://github.com/askarahodov/roboport/issues/448) |
| ADF-066 | Согласование начисления зарплаты | Фактическое начисление, выдача и остаток долга сотрудника создают атомарный документ и остаются сверяемыми. | [ADP-021](https://autodealer.ru/online/help/zarplata/raschet-zarplaty.html) | D09 / [#448](https://github.com/askarahodov/roboport/issues/448) |
| ADF-081 | План/факт и оплата сотрудников | Оплаченные часы не превышают подтверждённый факт, отредактированная смена сохраняет audit и требует утверждения. | [ADP-036](https://autodealer.ru/online/help/zarplata/tabel-ucheta-rabochego-vremeni.html) | D09 / [#448](https://github.com/askarahodov/roboport/issues/448) |

## Клиенты (4 критериев)

| Критерий | Действие | Acceptance (что должно реально работать) | Источник | Матрица / Issue |
|---|---|---|---|---|
| ADF-014 | Управление клиентами | Создать клиента и автомобиль с подтверждённой связью, затем проверить owner isolation при просмотре истории. | [ADP-014](https://autodealer.ru/online/help/klienty/index.html) | A01 / [#112](https://github.com/askarahodov/roboport/issues/112) |
| ADF-023 | Объединение клиентов | Безопасно объединить дубли клиентов без присвоения чужой истории, потерянных consent и некорректных связей. | [ADP-023](https://autodealer.ru/online/help/klienty/obedinenie-dublikatov.html) | A01 / [#112](https://github.com/askarahodov/roboport/issues/112) |
| ADF-048 | Договоры клиента | Создать и версионировать привязанные к клиенту договоры/госконтракты с корректным архивированием. | [ADP-048](https://autodealer.ru/online/help/klienty/dobavlenie-klienta/dopolnitelno.html) | D08 / [#112](https://github.com/askarahodov/roboport/issues/112) |
| ADF-089 | Госконтракт и архивирование | Архивированный клиентский договор остаётся доступен в старом подписанном документе, но не выбирается в новом. | [ADP-048](https://autodealer.ru/online/help/klienty/dobavlenie-klienta/dopolnitelno.html) | D08 / [#112](https://github.com/askarahodov/roboport/issues/112) |

## Мобильность (2 критериев)

| Критерий | Действие | Acceptance (что должно реально работать) | Источник | Матрица / Issue |
|---|---|---|---|---|
| ADF-010 | Мобильная запись | Сотрудник выполняет запись и изменение услуг со смартфона с действительной мобильной авторизацией и отзывом устройства. | [ADP-010](https://autodealer.ru/online/help/zapis-na-remont/zapis-v-mobilnom-prilozhenii.html) | E03 / [#118](https://github.com/askarahodov/roboport/issues/118) |
| ADF-042 | Мобильное приложение сотрудника | Войти в самостоятельное приложение и выполнить разрешённую операцию без обхода tenant/branch ACL. | [ADP-042](https://autodealer.ru/online/help/mobilnoe-prilozhenie/prilozhenie-avtodiler-onlain.html) | E03 / [#118](https://github.com/askarahodov/roboport/issues/118) |

## Настройки (4 критериев)

| Критерий | Действие | Acceptance (что должно реально работать) | Источник | Матрица / Issue |
|---|---|---|---|---|
| ADF-033 | Общие настройки СТО | Сохранить настройки таймзоны, рабочего графика и нормочаса с проверкой применимости к записи и документам. | [ADP-033](https://autodealer.ru/online/help/nastroiki/obshchie-nastroiki.html) | A06 / [#107](https://github.com/askarahodov/roboport/issues/107) |
| ADF-049 | Настройки модулей | Проверить на всех ролях доступность пользовательских настроек и сообщений, их сохранение и применение. | [ADP-049](https://autodealer.ru/online/help/nastroiki/index.html) | E01 / [#107](https://github.com/askarahodov/roboport/issues/107) |
| ADF-078 | Обязательные поля перед проведением | При включении обязательных полей менеджер/исполнитель/пробег/причина проверять их на сервере, а не только в UI. | [ADP-033](https://autodealer.ru/online/help/nastroiki/obshchie-nastroiki.html) | A06 / [#107](https://github.com/askarahodov/roboport/issues/107) |
| ADF-079 | Правила автостадий CRM | Из разных типов документов сделки создаются в указанных стадиях или не создаются согласно версии настроек. | [ADP-033](https://autodealer.ru/online/help/nastroiki/obshchie-nastroiki.html) | A06 / [#107](https://github.com/askarahodov/roboport/issues/107) |

## Обзор (1 критериев)

| Критерий | Действие | Acceptance (что должно реально работать) | Источник | Матрица / Issue |
|---|---|---|---|---|
| ADF-001 | Браузерная работа предприятия | Развернуть отдельную тестовую организацию и показать полный цикл входа и доступа к рабочему месту в браузере без установки desktop-клиента. | [ADP-001](https://autodealer.ru/online/help/o-programme.html) | A04 / [#107](https://github.com/askarahodov/roboport/issues/107) |

## Отчёты (7 критериев)

| Критерий | Действие | Acceptance (что должно реально работать) | Источник | Матрица / Issue |
|---|---|---|---|---|
| ADF-015 | Управленческие отчёты | Сформировать сводку по филиалу и периоду со сверкой каждой суммы до независимого первичного события. | [ADP-015](https://autodealer.ru/online/help/otchety-monitor-i-grafiki/otchety/index.html) | D10 / [#448](https://github.com/askarahodov/roboport/issues/448) |
| ADF-030 | Отчёты по товарам | Получить количественные остатки и движения с исходными партиями, стоимостью, резервами и drilldown. | [ADP-030](https://autodealer.ru/online/help/otchety-monitor-i-grafiki/otchety/otchety.-tovary.html) | D10 / [#448](https://github.com/askarahodov/roboport/issues/448) |
| ADF-037 | Общий монитор | Показать баланс, ДДС, клиентский поток, наряды и CRM-выполнение в едином обновляемом представлении. | [ADP-037](https://autodealer.ru/online/help/otchety-monitor-i-grafiki/monitor.html) | D11 / [#448](https://github.com/askarahodov/roboport/issues/448) |
| ADF-045 | ДДС и задолженность | Построить задолженность покупателей/поставщиков, связать неоплаченные документы и нераспределённые авансы без двойного счёта. | [ADP-045](https://autodealer.ru/online/help/otchety-monitor-i-grafiki/otchety/otchety.-dengi.html) | D10 / [#448](https://github.com/askarahodov/roboport/issues/448) |
| ADF-063 | Проверка прибыли наряда | Подтвердить себестоимость материалов, труд и поступления; суммы отчёта совпадают с первичкой и возвратами. | [ADP-015](https://autodealer.ru/online/help/otchety-monitor-i-grafiki/otchety/index.html) | D10 / [#448](https://github.com/askarahodov/roboport/issues/448) |
| ADF-082 | Drilldown финансового монитора | Выбор числа на сводке открывает ровно исходные разрешённые документы и не раскрывает чужие организации. | [ADP-037](https://autodealer.ru/online/help/otchety-monitor-i-grafiki/monitor.html) | D11 / [#448](https://github.com/askarahodov/roboport/issues/448) |
| ADF-087 | Авансы и задолженность | Нераспределённый аванс и непогашенный долг отображаются отдельно до явного зачёта в первичном документе. | [ADP-045](https://autodealer.ru/online/help/otchety-monitor-i-grafiki/otchety/otchety.-dengi.html) | D10 / [#448](https://github.com/askarahodov/roboport/issues/448) |

## Предприятие (4 критериев)

| Критерий | Действие | Acceptance (что должно реально работать) | Источник | Матрица / Issue |
|---|---|---|---|---|
| ADF-032 | Карточка предприятия | Создать юридически корректную карточку ИП/юрлица с адресом, реквизитами и связанными документами. | [ADP-032](https://autodealer.ru/online/help/predpriiatiia.html) | A04 / [#107](https://github.com/askarahodov/roboport/issues/107) |
| ADF-075 | История налогового режима | Новая версия режима действует с даты, старые счета сохраняют старый расчёт и неподтверждённая налоговая политика блокирует проведение. | [ADP-032](https://autodealer.ru/online/help/predpriiatiia.html) | A04 / [#107](https://github.com/askarahodov/roboport/issues/107) |
| ADF-076 | Банковские счета компании | Установить основной счёт, архивировать предыдущий и убедиться, что новые документы не выбирают архивный реквизит. | [ADP-032](https://autodealer.ru/online/help/predpriiatiia.html) | A04 / [#107](https://github.com/askarahodov/roboport/issues/107) |
| ADF-077 | Закрытый период филиала | Настроенный период блокирует неверные задним числом проводки; rights bypass, time-zone boundary и race отказывают. | [ADP-032](https://autodealer.ru/online/help/predpriiatiia.html) | A04 / [#107](https://github.com/askarahodov/roboport/issues/107) |

## Приёмка (5 критериев)

| Критерий | Действие | Acceptance (что должно реально работать) | Источник | Матрица / Issue |
|---|---|---|---|---|
| ADF-006 | Акт осмотра автомобиля | Сохранить версию осмотра автомобиля с пробегом, состоянием, повреждениями и безопасным просмотром владельцем заказа. | [ADP-006](https://autodealer.ru/online/help/zakaz-nariady/sozdanie-zakaz-nariada/zakaz-nariad.-akt-osmotra.html) | B02 / [#439](https://github.com/askarahodov/roboport/issues/439) |
| ADF-011 | Диагностический лист | Создать диагностический лист с контролируемыми дефектами по узлам и версией, связать с заказом и сметой. | [ADP-011](https://autodealer.ru/online/help/diagnosticheskii-list-neispravnosti/index.html) | B03 / [#439](https://github.com/askarahodov/roboport/issues/439) |
| ADF-040 | Настройки диагностики | Сконфигурировать проверочные пункты и ролевые права, применить шаблон к диагностическому документу. | [ADP-040](https://autodealer.ru/online/help/diagnosticheskii-list-neispravnosti/nastroiki-diagnosticheskikh-listov.html) | B03 / [#439](https://github.com/askarahodov/roboport/issues/439) |
| ADF-056 | Фотографии зон осмотра | Привязать снимки повреждений к версии акта и обеспечить revoke-aware просмотр без утечки иной организации. | [ADP-006](https://autodealer.ru/online/help/zakaz-nariady/sozdanie-zakaz-nariada/zakaz-nariad.-akt-osmotra.html) | B02 / [#439](https://github.com/askarahodov/roboport/issues/439) |
| ADF-060 | Повторный диагностический осмотр | Сравнить две версии дефектовки и убедиться, что старое решение клиента не переносится на новую версию автоматически. | [ADP-011](https://autodealer.ru/online/help/diagnosticheskii-list-neispravnosti/index.html) | B03 / [#439](https://github.com/askarahodov/roboport/issues/439) |

## Ремонт (12 критериев)

| Критерий | Действие | Acceptance (что должно реально работать) | Источник | Матрица / Issue |
|---|---|---|---|---|
| ADF-002 | Журнал заказ-нарядов | Открыть список нарядов организации, применить поиск и фильтр, прочитать работы/товары/платежи документа и отказать чужому филиалу. | [ADP-002](https://autodealer.ru/online/help/zakaz-nariady/index.html) | B01 / [#441](https://github.com/askarahodov/roboport/issues/441) |
| ADF-003 | Создание заказ-наряда | Создать наряд из подтверждённого клиента и автомобиля, сохранить позиции и открыть документ повторно без потери связей. | [ADP-003](https://autodealer.ru/online/help/zakaz-nariady/sozdanie-zakaz-nariada/index.html) | B01 / [#441](https://github.com/askarahodov/roboport/issues/441) |
| ADF-004 | Работы внутри наряда | Добавить нормативную работу, назначить исполнителя и пересчитать согласованную сумму с проверкой прав на цену. | [ADP-004](https://autodealer.ru/online/help/zakaz-nariady/sozdanie-zakaz-nariada/zakaz-nariad.-raboty.html) | B05 / [#167](https://github.com/askarahodov/roboport/issues/167) |
| ADF-005 | Товары внутри наряда | Добавить складскую номенклатуру к наряду и подтвердить резерв/выдачу без отрицательного остатка. | [ADP-005](https://autodealer.ru/online/help/zakaz-nariady/sozdanie-zakaz-nariada/zakaz-nariad.-tovary.html) | C05 / [#168](https://github.com/askarahodov/roboport/issues/168) |
| ADF-012 | Каталог работ и комплексов | Собрать комплекс связанных операций, материалов и цен, применить его к наряду с корректной общей суммой. | [ADP-012](https://autodealer.ru/online/help/raboty-i-kompleksy/index.html) | B05 / [#167](https://github.com/askarahodov/roboport/issues/167) |
| ADF-050 | Параллельные статусы наряда | Проверить сочетания NEW/DRAFT/IN_WORK/DONE/POSTED/CLOSED, отмену проведения, запрет неразрешённых переходов и временную историю. | [ADP-002](https://autodealer.ru/online/help/zakaz-nariady/index.html) | B01 / [#441](https://github.com/askarahodov/roboport/issues/441) |
| ADF-051 | Массовые действия с нарядами | Создать счёт по нескольким нарядам только одного клиента; смешанные клиенты и роли завершаются атомарным отказом. | [ADP-002](https://autodealer.ru/online/help/zakaz-nariady/index.html) | B01 / [#441](https://github.com/askarahodov/roboport/issues/441) |
| ADF-052 | Экспорт УПД в ЭДО | Подготовить юридически валидный XML УПД и ZIP для нескольких документов, проверить формат и права выгрузки. | [ADP-002](https://autodealer.ru/online/help/zakaz-nariady/index.html) | B01 / [#441](https://github.com/askarahodov/roboport/issues/441) |
| ADF-053 | Привязка документов к наряду | После добавления заказа сформировать счёт и акт на основе подтверждённых строк и проверить traceability. | [ADP-003](https://autodealer.ru/online/help/zakaz-nariady/sozdanie-zakaz-nariada/index.html) | B01 / [#441](https://github.com/askarahodov/roboport/issues/441) |
| ADF-054 | Исполнители и нормочасы | Назначить на каждую услугу исполнителя и проверить фактическую выработку и действовавшую ставку. | [ADP-004](https://autodealer.ru/online/help/zakaz-nariady/sozdanie-zakaz-nariada/zakaz-nariad.-raboty.html) | B05 / [#167](https://github.com/askarahodov/roboport/issues/167) |
| ADF-055 | Товары клиента и расходники | Отличить клиентскую запчасть, реализацию и расходник услуги; не списать чужой или неучтённый материал. | [ADP-005](https://autodealer.ru/online/help/zakaz-nariady/sozdanie-zakaz-nariada/zakaz-nariad.-tovary.html) | C05 / [#168](https://github.com/askarahodov/roboport/issues/168) |
| ADF-061 | Штрихкоды работ | По отсканированному коду найти разрешённую операцию и записать её ровно один раз в наряд. | [ADP-012](https://autodealer.ru/online/help/raboty-i-kompleksy/index.html) | B05 / [#167](https://github.com/askarahodov/roboport/issues/167) |

## Склад (13 критериев)

| Критерий | Действие | Acceptance (что должно реально работать) | Источник | Матрица / Issue |
|---|---|---|---|---|
| ADF-017 | Справочник номенклатуры | Создать товар с безопасными идентификаторами, производителем и ценой; корректно отобразить документные и складские связи. | [ADP-017](https://autodealer.ru/online/help/nomenklatura/index.html) | C03 / [#168](https://github.com/askarahodov/roboport/issues/168) |
| ADF-020 | Карточка номенклатуры | Создать номенклатуру с номерами, единицей измерения и корректной категорией цены; исключить дубликаты. | [ADP-020](https://autodealer.ru/online/help/nomenklatura/sozdanie-nomenklatury.html) | C03 / [#168](https://github.com/askarahodov/roboport/issues/168) |
| ADF-024 | Объединение номенклатуры | Объединить дубли SKU с сохранением документной истории, партий, резервов и себестоимости без двойного учёта. | [ADP-024](https://autodealer.ru/online/help/nomenklatura/obedinenie-dublikatov.html) | C03 / [#168](https://github.com/askarahodov/roboport/issues/168) |
| ADF-025 | Инвентаризация | Пересчитать фактические остатки по складу и партии, записать утверждённые расхождения атомарно и сохранить audit. | [ADP-025](https://autodealer.ru/online/help/skladskoi-uchet/inventarizatciia.html) | C08 / [#168](https://github.com/askarahodov/roboport/issues/168) |
| ADF-028 | Импорт и экспорт SKU | Импортировать файл с проверкой колонок, неверных данных, dry-run, идемпотентностью и экспортом без CSV/XLSX-инъекций. | [ADP-028](https://autodealer.ru/online/help/nomenklatura/import-i-eksport-nomenklatury.html) | E04 / [#172](https://github.com/askarahodov/roboport/issues/172) |
| ADF-029 | Заказы клиентов и поставщикам | Создать потребность клиента, сформировать заказ поставщику и сверить частичный приход/отмену по позициям. | [ADP-029](https://autodealer.ru/online/help/skladskoi-uchet/protcenka/zakaz-klienta.-zakaz-postavshchiku.html) | C09 / [#115](https://github.com/askarahodov/roboport/issues/115) |
| ADF-038 | Приход товаров из CSV | Сопоставить столбцы CSV, проверить ошибки/совпадения и провести подтверждённый приход по партиям без двойного импорта. | [ADP-038](https://autodealer.ru/online/help/skladskoi-uchet/prikhod-tovara/prikhod-tovarov-iz-csv-faila.html) | E04 / [#172](https://github.com/askarahodov/roboport/issues/172) |
| ADF-039 | Печать ценников | Выбрать шаблон, формат номера/штрихкода и распечатать ценники по выбранным товарам с проверкой макета. | [ADP-039](https://autodealer.ru/online/help/skladskoi-uchet/nastroika-i-pechat-tcennikov.html) | C10 / [#168](https://github.com/askarahodov/roboport/issues/168) |
| ADF-046 | История поставщика | Открыть поставщика и сверить его документы, возвраты, взаиморасчёты и остатки по правам. | [ADP-046](https://autodealer.ru/online/help/postavshchiki/statistika-po-postavshchiku.html) | C09 / [#115](https://github.com/askarahodov/roboport/issues/115) |
| ADF-064 | Массовое изменение цен SKU | Пересчитать цены группы от категории или приходной цены с проверкой округления, прав и истории изменений. | [ADP-017](https://autodealer.ru/online/help/nomenklatura/index.html) | C03 / [#168](https://github.com/askarahodov/roboport/issues/168) |
| ADF-070 | Негативный сценарий импорта | Неверный файл/колонка/формула/ссылка на внешнее значение приводит к нулю записей и отчёту без ПДн. | [ADP-028](https://autodealer.ru/online/help/nomenklatura/import-i-eksport-nomenklatury.html) | E04 / [#172](https://github.com/askarahodov/roboport/issues/172) |
| ADF-071 | Частичная поставка и резерв | Частичный приход обновляет остатки и статус позиции, повтор того же документа не создаёт дополнительных партий. | [ADP-029](https://autodealer.ru/online/help/skladskoi-uchet/protcenka/zakaz-klienta.-zakaz-postavshchiku.html) | C09 / [#115](https://github.com/askarahodov/roboport/issues/115) |
| ADF-083 | CSV маппинг и результаты импорта | Распознанные совпадения SKU проверяются человеком перед проведением; повторное подтверждение идемпотентно. | [ADP-038](https://autodealer.ru/online/help/skladskoi-uchet/prikhod-tovara/prikhod-tovarov-iz-csv-faila.html) | E04 / [#172](https://github.com/askarahodov/roboport/issues/172) |

## Финансы (8 критериев)

| Критерий | Действие | Acceptance (что должно реально работать) | Источник | Матрица / Issue |
|---|---|---|---|---|
| ADF-007 | Платежи по заказ-наряду | Провести реальную привязанную к наряду частичную оплату и возврат с первичным подтверждением и недопущением двойной оплаты. | [ADP-007](https://autodealer.ru/online/help/zakaz-nariady/sozdanie-zakaz-nariada/zakaz-nariad.-platezhi.html) | D06 / [#445](https://github.com/askarahodov/roboport/issues/445) |
| ADF-022 | Отчёт ДДС | Показать реальные денежные приходы/расходы по предприятию, счёту, периоду и статьям с точными открывающим и закрывающим остатками. | [ADP-022](https://autodealer.ru/online/help/dengi/dvizhenie-denezhnykh-sredstv.html) | D10 / [#445](https://github.com/askarahodov/roboport/issues/445) |
| ADF-044 | Финансовые документы | Создать реальный приход или расход с платёжной статьёй, контрагентом и проектом; документ отразится в сальдо и отчёте. | [ADP-044](https://autodealer.ru/online/help/dengi/index.html) | D05 / [#445](https://github.com/askarahodov/roboport/issues/445) |
| ADF-047 | Финансовые проекты | Сохранить центр доходов/расходов, разнести операцию по проекту и проверить отчёты и ограничения доступа. | [ADP-047](https://autodealer.ru/online/help/obshchie-spravochniki/finansy/proekty.html) | D10 / [#170](https://github.com/askarahodov/roboport/issues/170) |
| ADF-057 | Связь денег и закрытия | При возврате оплаты восстановить правильный долг и не открыть незаконно закрытый документ. | [ADP-007](https://autodealer.ru/online/help/zakaz-nariady/sozdanie-zakaz-nariada/zakaz-nariad.-platezhi.html) | D06 / [#445](https://github.com/askarahodov/roboport/issues/445) |
| ADF-067 | Сверка открывающего остатка ДДС | Показать opening+in-out=closing для каждого счёта с drilldown и запретом смешивания кассовых и складских сумм. | [ADP-022](https://autodealer.ru/online/help/dengi/dvizhenie-denezhnykh-sredstv.html) | D10 / [#445](https://github.com/askarahodov/roboport/issues/445) |
| ADF-086 | Разделение поступления по статьям | Один приход распределён между погашением долга и авансом так, что общий баланс не удваивается. | [ADP-044](https://autodealer.ru/online/help/dengi/index.html) | D05 / [#445](https://github.com/askarahodov/roboport/issues/445) |
| ADF-088 | Сверка по центру расходов | Финансовая операция отражается в выбранном проекте, исключает смешивание филиалов и допускает drilldown. | [ADP-047](https://autodealer.ru/online/help/obshchie-spravochniki/finansy/proekty.html) | D10 / [#170](https://github.com/askarahodov/roboport/issues/170) |

## CRM (12 критериев)

| Критерий | Действие | Acceptance (что должно реально работать) | Источник | Матрица / Issue |
|---|---|---|---|---|
| ADF-013 | CRM сделки | Создать сделку по обращению, связать запись и наряд и отследить её до подтверждённой оплаты. | [ADP-013](https://autodealer.ru/online/help/crm/index.html) | D01 / [#169](https://github.com/askarahodov/roboport/issues/169) |
| ADF-018 | Воронка продаж | Отобразить сделки по этапам и причинам, включая конверсию успешных и потерянных сделок за выбранный период. | [ADP-018](https://autodealer.ru/online/help/crm/voronka-prodazh.html) | D02 / [#169](https://github.com/askarahodov/roboport/issues/169) |
| ADF-026 | Настраиваемый канбан CRM | Создать рабочий этап, настроить разрешённые переходы и причины, проверить изменение сделки через контролируемое действие. | [ADP-026](https://autodealer.ru/online/help/crm/kanban.html) | D01 / [#169](https://github.com/askarahodov/roboport/issues/169) |
| ADF-062 | CRM автосоздание по документам | Создание наряда или записи по настроенному правилу создаёт либо обновляет только одну сделку своего филиала. | [ADP-013](https://autodealer.ru/online/help/crm/index.html) | D01 / [#169](https://github.com/askarahodov/roboport/issues/169) |
| ADF-065 | Анализ причин CRM-отказов | По фильтру периода и менеджера получить исходные причины всех отказов и корректные доли без дублирования сделок. | [ADP-018](https://autodealer.ru/online/help/crm/voronka-prodazh.html) | D02 / [#169](https://github.com/askarahodov/roboport/issues/169) |
| ADF-068 | Ограниченные переходы канбана | Разрешить перемещение только в заданные этапы и с допустимой причиной; отклонённый переход не меняет историю. | [ADP-026](https://autodealer.ru/online/help/crm/kanban.html) | D01 / [#169](https://github.com/askarahodov/roboport/issues/169) |
| ADF-069 | Автоматический перевод просроченной сделки | Настроенный дедлайн переносит карточку ровно один раз, не обходя запретные стадии и послеоперационный аудит. | [ADP-026](https://autodealer.ru/online/help/crm/kanban.html) | D01 / [#169](https://github.com/askarahodov/roboport/issues/169) |
| ADF-090 | Создание сделки по любому обращению | Создать сделку автоматически по звонку, заявке сайта или личному визиту, а также вручную; зафиксировать клиента, автомобиль, источник, причину, этап, приоритет и дедлайн. | [ADP-052](https://autodealer.ru/online/help/crm/sdelki.html) | D01 / [#169](https://github.com/askarahodov/roboport/issues/169) |
| ADF-091 | История сделки и связанные документы | Сохранять историю комментариев и владельца сделки, связывать её с калькуляцией, записью, заказ-нарядом, товарным чеком, расходной накладной, заказом клиента, возвратом, счётом и диагностическим листом. | [ADP-052](https://autodealer.ru/online/help/crm/sdelki.html) | D01 / [#169](https://github.com/askarahodov/roboport/issues/169) |
| ADF-092 | Рекомендации работ и товаров по автомобилю | Для конкретного автомобиля клиента сохранять отложенные работы: вручную или из справочника, с возможностью убрать ненужные связанные позиции; товары выбирать из номенклатуры, склада или комплексов. Не смешивать автомобильные рекомендации с CRM-заметкой. | [ADP-050](https://autodealer.ru/online/help/rekomendatcii-rekomendovannye-tovary-i-raboty.html) | B08 / [#169](https://github.com/askarahodov/roboport/issues/169) |
| ADF-093 | Журнал звонков и приложение телефонии | Показывать caller ID и входящее уведомление, связывать номер с клиентом, исключать нежелательные номера, вести журнал входящих/исходящих/пропущенных/непросмотренных звонков и инициировать исходящий вызов. | [ADP-051](https://autodealer.ru/online/help/mobilnoe-prilozhenie/prilozhenie-zvonki.html) | D04 / [#169](https://github.com/askarahodov/roboport/issues/169) |
| ADF-094 | Атрибуция сделки по рекламной кампании | Создать кампанию с названием, номером, периодом и примечанием; входящий звонок через подключённую телефонию должен связать создаваемую сделку с действовавшей кампанией. | [ADP-053](https://autodealer.ru/online/help/obshchie-spravochniki/raznoe/reklamnye-kampanii.html) | D01 / [#169](https://github.com/askarahodov/roboport/issues/169) |

## Незаменимые сквозные сценарии

1. **Наряд → деньги → закрытие:** реальный клиент и автомобиль, запись на пост, осмотр/смета/согласие, работа и запасные части, независимый QC, законная оплата/возврат, акты/УПД, подтверждённая выдача, документный audit. Отдельные synthetic прототипы **не** заменяют полный процесс.
2. **Закупка → склад → наряд:** лицензированная применяемость OEM/аналогов, проценка, поставщик, договорная цена, заказ/частичный приход, партия/резерв/выдача/возврат, себестоимость и налоговые документы без отрицательных остатков.
3. **CRM и каналы:** звонок/чат/форма → клиент и consent → уникальная сделка → автопереход/ответственный → запись → подтверждённая оплата → аналитика с воспроизводимым lineage.
4. **Финансы и персонал:** реальные кассы/банковские выписки, авансы/зачёты, ККТ/ЭДО, разнесение по проектам, дебиторка/кредиторка, учёт подтверждённых смен, версионированные тарифы, расчёт/выдача зарплаты.
5. **Мультиорганизационная защита и эксплуатация:** реальные роли и отзы́в, история прав, юридическое владение автомобилем, филиальная изоляция, миграция клиента/склада/документов с согласиями, восстановление, собственная поддерживаемая Docsify база знаний.

### Запрет ложной полноты

Не закрывать [#108](https://github.com/askarahodov/roboport/issues/108), [#107](https://github.com/askarahodov/roboport/issues/107) или продуктовые эпики #112–#172 по одному зелёному тесту или синтетической демонстрации. Точное дерево Online Help, обновления и все вложенные операции всё ещё требуют исчерпывающего независимого обхода; текущее перечисление — **проверяемая начальная спецификация**, а не готовый продукт.

**Как обновлять:** добавить исходную страницу в `AUTODEALER_ONLINE_PAGES.json` с её реальной датой проверки → описать каждое независимое действие в `AUTODEALER_FUNCTION_REQUIREMENTS.json` с источником, владельцем и acceptance → выполнить `python scripts/check_autodealer_audit.py` → реализовать UI/API/БД/тесты → привязать фактические evidence. Для мелких docs-изменений действует [impact-based CI](../operations/DEV_ENVIRONMENT.md), а перед релизом — полный обязательный CI на `main`.
