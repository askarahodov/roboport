# Атомарный synthetic cutover: автомобили, услуги, SKU и склад (#172)

**Статус: current-dev / закрытая PG17-библиотека, не production-инструмент.** Это реализованная транзакционная процедура для **искусственных CSV**, а не миграция существующей базы реальных клиентов. Закрытый браузерный сценарий описан ниже. Нет публичного API, доступа к настоящим VIN/ПДн или переноса ремонтной истории. Любой production cutover и выдача новых прав заблокированы #119/#165/#172 до независимых approvals.

## Что делает один вызов

Библиотека `apps/api/roboport_api/auto_synthetic_cutover.py` принимает **три проверенных CSV** и их заранее известные SHA-256:

1. `VEHICLE`: `record_type,source_id,organization_id,location_id,vin,make,model,source_system`. Существующий parser запрещает клиентские связи/историю и требует валидную VIN-форму; *доказательство права собственности этим не создаётся*.
2. `SERVICE`: `record_type,source_system,source_id,code,title,duration_minutes`. Услуги активируются в неизменяемом каталоге **своего** филиала, который позднее используется при создании наряда. Нет цен, нормочасовых ставок и проведённых работ.
3. `SKU/STOCK`: `record_type,source_id,organization_id,location_id,article,quantity`. `SKU` объявляет новый артикул с `quantity=0`; `STOCK` содержит положительные или нулевые исходные остатки того же артикула. Отрицательные, чужие и не объявленные SKU запрещены. Нулевая строка получает запись происхождения, но не создаёт нулевую складскую проводку. `STOCK > 0` в новой транзакции становится **физической проводкой `RECEIPT`**, не `RESERVE`, без продажи/цены и автоматического размещения по ячейкам/серийным номерам.

Вначале функция `rehearse_synthetic_cutover(conn, token, organization_id, location_id, sources, synthetic_only=True)` требует живую сессию сотрудника с **обеими** `MANAGER` и `WAREHOUSE` на том же филиале. Сессионные, tenant и grant-строки блокируются против конкурентного отзыва. Затем в **одной PostgreSQL-транзакции** выполняются строгая валидация SHA/CSV, staging трёх пакетов, поштучная активация каталога автомобилей без ownership, каталога работ и склада, и независимая сверка происхождения каждой строки и итоговых сумм. Ошибка любого этапа откатывает **все** новые staging/activation, автомобили, услуги, SKU и `RECEIPT` из этого вызова.

При повторе тех же `batch_id` и точно тех же байтов source/staging, после повторной проверки live grants, история не удваивается: `replayed:true,writes:0`. Важный VIN-инвариант: рабочий VIN после **собственного** promotion больше не считается чужим дубликатом, но replay отвергается, если VIN принадлежит другой записи без provenance этого пакета. Изменённый файл, digest, чужой филиал, повторный артикул в живом складе или каталогный конфликт отклоняются без автоматического слияния. Для повторного чтения без создания объектов `reconcile_synthetic_cutover(...)` возвращает только counts и `productionApproved:false`.

## Документирование warehouse provenance

Новая Alembic `0094_auto_inventory_activation` добавляет append-only `auto_migration_stock_activation`. **Каждая** строка staging `(batch_id,line_number)` связана с `auto_stock_sku` своей организации/филиала и реальным `auto_stock_event RECEIPT` для `STOCK > 0`. Защита trigger `BEFORE INSERT` проверяет `record_type,article,tenant,location,quantity,sku,event.action` и требует, чтобы строка `STOCK` ссылалась на SKU именно **этого пакета**. `UPDATE/DELETE` запрещены на уровне PG17, даже если запись пытаются переписать напрямую. Downgrade миграции fail-closed при существующей истории активации: сначала должна быть подтверждённая изолированная копия и процедура восстановления, не `DROP` и не rewrite audit.

Для двух SKU из 5 исходных строк (3 STOCK, из них одна нулевая) будет создано **5 строк неизменяемого provenance**, **2 SKU** и **2 физических RECEIPT** с исходными количествами. Ни цена закупки, ни бухгалтерская проводка, ни поставщик не выводятся из CSV; полноценные склады/bin/lot/series, налоговые и финансовые документы — planned и требуют отдельного утверждения.

## Как QA выполняет воспроизводимый synthetic-прогон

Здесь нет команды, способной без контекста подгрузить произвольный CSV в рабочую базу. Реальная точка входа — **внутренний код**, доступный только в disposable PG17 CI после migrations и выдачи искусственных manager/warehouse grants. Для подтверждения достаточно запустить:

```bash
# Только disposable PostgreSQL 17 с тестовыми фикстурами;
# переменная ROBOport_DATABASE_URL указывает на локальную тестовую базу.
PYTHONPATH=apps/api python -m pytest -q -ra tests/integration/test_pg_auto_synthetic_cutover.py
```

Тест из одного каталога временных файлов создаёт VEHICLE/SERVICE/SKU+STOCK CSV с подставными идентификаторами и проверяет их полный путь до живого каталога и складского ledger, корректный повтор без удвоения, отсутствие связки `auto_vehicle_customer_link`, отказ чужим филиалам, двум невыданным ролям и отозванному `WAREHOUSE`, атомарный откат при конфликте каталога услуг **или** уже существующем SKU, полный rollback внешней транзакции, отрицательные прямые SQL-попытки изменить immutable provenance, и конкурентное выполнение идентичного пакета. Linux CI обязательно проходит `impact`, `docs`, `postgres`, `api`, `web`, `browser-e2e`, вместе с недавним реальным синтетическим `pg_dump/pg_restore` checkpoint для live WorkOrder/stock.

Неверный результат — исключение с **общим кодом** `FORBIDDEN`, `BATCH_SCOPE_MISMATCH`, `BATCH_INCONSISTENT`, `ARTICLE_CONFLICT`, `ACTIVATION_MISMATCH` и др.; наружу **не выводятся** VIN, исходные строки CSV, имена клиентов, телефон, VIN lookup или article в тексте исключения. Не включать raw PG SQL DETAIL в пользовательскую ошибку.

## Важные ограничения и восстановление

- Реальные автомобили не становятся собственностью клиента от совпадения VIN; ни staged, ни activated VIN не создают `auto_vehicle_customer_link`, не дают доступ к истории, документам и старым `service_ticket`. Это отдельный объектно-авторизованный и юридически проверяемый процесс.
- Здесь нет переноса **клиентов, контрагентов, фото, актов, истории ремонта, документов или XLSX напрямую в БД**. XLSX приводится к каноническим CSV (VEHICLE/SERVICE — также существующим offline preflight) **до** этого строго доверенного synthetic-этапа.
- До commit весь bundle является одной транзакцией и может быть полностью откатан. После commit события и происхождение **не удалять**: безопасное восстановление — отдельная изолированная резервная копия и утверждённый reconciliation/cutover план, не SQL DELETE.
- Защита DB от неуполномоченного owner-level SQL не является полной production моделью least privilege/RLS. В dev тестовый PostgreSQL пользователь владеет схемой. Реальные DB grants, PII/legal/consent, независимый security review, backup encryption, retention, RPO/RTO, cutover plan, физическая складская инвентаризация и security/legal/ops sign-off остаются **обязательными и не выполненными**. #172 и #119 остаются OPEN до их подтверждения.

Связанные источники: [миграция и импорт](DATA_MIGRATION.md), [восстановление live WorkOrder](RESTORE_CHECKPOINT.md), [права филиалов](ORGANIZATIONS.md), [план выхода из legacy](https://github.com/askarahodov/roboport/issues/165) и [функциональная матрица](FUNCTIONAL_MATRIX.md).


## Доказательство полного восстановления этого же пакета (CI, #172/#119)

В той же обязательной `postgres` job, перед её существующим `pg_dump -Fc`, инструмент `scripts/auto_cutover_restore_probe.py` дополнительно активирует независимый синтетический пакет из всех трёх доменов. Отдельный приватный manifest `/tmp/roboport-auto-cutover-restore.json` (создаётся без overwrite, права 0600, **не входит в dump**) хранит SHA-256 фактических связей staged→live: исходная VIN-строка и ровно одна активная машина без владельца, две услуги с неизменяемым source, пять SKU/STOCK-строк с реальными RECEIPT и остатками. После `pg_restore --exit-on-error` скрипт сравнивает checksum original и `roboport_restored`. На **clone** выполняет SQL отрицательные проверки immutable vehicle/service/stock lineage и stock tenant-scope; checksum должен остаться прежним. Печатаются только `SYNTHETIC_CUTOVER_RESTORE_SEED_OK` и `SYNTHETIC_CUTOVER_RESTORE_VERIFY_OK`, не VIN/source/токены. Команды (исключительно в доверенной disposable CI среде):

```bash
PYTHONPATH="apps/api:tests/integration" python -m scripts.auto_cutover_restore_probe seed --manifest /tmp/roboport-auto-cutover-restore.json
# После pg_dump -Fc из roboport и pg_restore в отдельную roboport_restored:
PYTHONPATH="apps/api:tests/integration" python -m scripts.auto_cutover_restore_probe verify --manifest /tmp/roboport-auto-cutover-restore.json --database roboport
PYTHONPATH="apps/api:tests/integration" python -m scripts.auto_cutover_restore_probe verify-clone --manifest /tmp/roboport-auto-cutover-restore.json --database roboport_restored
```

Назначение БД жёстко ограничено унаследованным от `auto_restore_lifecycle_probe._dsn` `127.0.0.1:5432` и двумя локальными CI-именами, без libpq URI query/environment overrides. `verify-clone` никогда не запускается для источника; диагностические ошибки не раскрывают payload. Тесты `tests/contract/test_auto_cutover_restore_probe.py` проверяют manifest/хеш/запрет переписи. Это доказательство **только synthetic восстановления**, не реального customer/legal cutover или утверждённой политики RPO/RTO.


### Усиление безопасности после независимого review

При самостоятельной `reconcile_synthetic_cutover` **требуются все три исходных файла**: автомобиль читается один раз через `O_NOFOLLOW`/`fstat` с проверкой размера, SHA-256 и синтаксиса именно этого снимка; услуги и склад сверяются своими существующими strict/no-follow читателями. Затем *каждая* исходная строка сопоставляется с сохранённой staging строкой по номеру, области, происхождению, содержимому и количеству. Наконец, SHA всех трёх staging batch сверяются с переданным manifest и живые promotion/RECEIPT ссылаются на ту же историю. Отсутствующий файл, изменённые байты, подменённый ожидаемый SHA, символическая ссылка или другая организация дают отказ, а не `ok:true` по совпадению counts. Эта independent reconciliation не пишет данные в PostgreSQL; она повторно проверяет доверенный источник.

При Alembic downgrade `0094` получает `ACCESS EXCLUSIVE` lock **перед** проверкой существования provenance. Конкурентная активация держит `ROW EXCLUSIVE`; поэтому downgrade не может прочитать пустую таблицу, затем дождаться commit новых RECEIPT и удалить их историю. Если история есть — обязательный `RuntimeError` и PostgreSQL rollback миграционного шага. Это проверено отдельным конкурентным PG17-тестом, а не только статическим анализом текста миграции.


## Сверенный приватный экспорт пакета

После атомарного synthetic cutover три исходных CSV можно выгрузить **в одном проверенном комплекте** с `manifest.json` через `auto_synthetic_cutover_export.export_reconciled_synthetic_cutover`. Это read-only SQL-сверка того же source digest, staging и live-продвижения под живой авторизацией MANAGER+WAREHOUSE; отклонение любого поля запрещает выдачу пакета. Только искусственные VIN, без автоматического owner/history. Получатель может повторно сверить SHA-256 байтов без БД: `PYTHONPATH=apps/api:. python scripts/auto_cutover_export_verify.py --synthetic-only --expect-manifest-sha256 <64-lowercase-hex> ./synthetic-cutover-export`. Права файлов 0600, каталога 0700; скопированный комплект нельзя публиковать. [Полная инструкция, негативные тесты и ограничения](DATA_MIGRATION.md). Реальный экспорт персональных данных, разрешение на передачу и production cutover — `planned`, #119/#165/#172 остаются открытыми.


**Содержательный контроль полученного пакета:** offline `auto_cutover_export_verify.py` отклоняет и пакет с правильно пересчитанными SHA, если в CSV есть неподходящий VIN, чужой филиал/tenant, дублированный source, услуга с недопустимой длительностью, stock без SKU, неверное количество или поля/счётчики манифеста не совпадают с CSV. Trusted режим требует переданный отдельно manifest SHA и возвращает `manifestDigestVerified:true` только при совпадении; `--structure-only` — диагностический режим с false, не приёмка передачи. Это не подпись, не право на историю и не legal approval. Семантические и файловые отрицательные проверки — `tests/contract/test_auto_cutover_export_verify.py` без PG; сквозная авторизованная выгрузка и проверка хеша — `tests/integration/test_pg_auto_synthetic_cutover_export.py`.


## Закрытая репетиция в браузере (#172, current-dev)

На отдельном disposable Linux/PG17 стенде с прежними synthetic HTTPS-флагами дополнительно включите `ROBOport_SYNTHETIC_CUTOVER_UI=1`. По умолчанию API возвращает 404. Требуются одновременно MANAGER и WAREHOUSE на одном филиале, живая сессия и CSRF. Экран `/staff/auto-cutover`: UUID организации/филиала → создать искусственный комплект или выбрать три CSV → подтвердить synthetic → предварительная проверка (0 записей) → явное подтверждение → импорт → независимая сверка → ZIP. Preview проверяет структуру, scope и SHA исходников, не обещает отсутствие DB-конфликта. Повтор точных байтов в том же филиале создаёт тот же набор batch UUID; конфликт существующего VIN/кода/артикула не сливает записи и не меняет остатки. Перед импортом сохраните все исходные CSV: автоматически созданный комплект существует только в памяти страницы. После потери ответа заново выберите те же файлы и выполните сверку или повтор с прежними байтами. Не генерируйте новый комплект для повтора.

Четыре операции основного цикла POST `/api/v1/dev/auto/organizations/{org}/locations/{loc}/cutover/{operation}` (preview/commit/reconcile/export) скрыты из публичного OpenAPI. JSON `{syntheticOnly:true,files:{"vehicles.csv":{base64,sha256},"services.csv":{base64,sha256},"inventory.csv":{base64,sha256}},bundleSha256?}`. Для всех операций кроме preview обязателен возвращённый сервером bundleSha256; сервер повторно вычисляет его из scope и точных bytes. Это идентификатор содержимого, не токен авторизации. Каждый CSV до 1 000 000 байт и 2000 строк, тело до 4 100 000 байт. В браузерном режиме обязательны искусственные маркировки (DEMO, SYNTHETIC, TEST, SYN-), не являющиеся доказательством отсутствия ПДн. 422 — формат/хеш/чужой scope данных; 403 — живые права или CSRF; 409 — конфликт/сверка; 404 — режим выключен. Ошибки не содержат исходные строки. Нет произвольных файловых путей.

Export возвращает четыре файла с фиксированными именами в ZIP, `X-Cutover-SHA256` и `X-Cutover-Manifest-SHA256`. Сервер независимо сверяет каталог и манифест, UI проверяет точные байты ZIP перед сохранением. Получателю передайте SHA манифеста отдельно; распакуйте только четыре ожидаемых файла в новый приватный каталог 0700, назначьте файлам 0600, затем запустите существующий offline verifier. Не распаковывайте неизвестные архивы в рабочую БД. Откат UI/API: выключить флаг, не удалять immutable provenance. Реальные источники, ownership, legal, production restore, RPO/RTO остаются planned.


## Подготовка смешанного комплекта CSV/XLSX (#172)

На том же закрытом экране можно выбрать для каждого из трёх каталогов CSV или простой XLSX (один видимый лист, без формул, макросов и внешних ссылок). До 1 МБ и 2000 строк на каталог. Если заголовки отличаются, введите их точные имена через запятую **в порядке показанных полей назначения**; требуется взаимно однозначное соответствие всех колонок. Лишние, пропущенные и дублированные поля отклоняются, значения не угадываются. Для канонического CSV без mapping исходные байты сохраняются.

Нажмите «Подготовить CSV/XLSX». Закрытый POST `cutover/prepare` требует прежние live MANAGER+WAREHOUSE, CSRF и synthetic-флаги, проверяет SHA каждого исходника и преобразует все три файла, затем применяет прежние проверки scope, VIN, дублей, stock totals и synthetic markers. Возвращает канонические CSV с SHA, counts и bundle identity, writes=0, productionApproved=false; не сохраняет исходники или mapping в БД. UI проверяет SHA всех полученных файлов до замены комплекта, не допускает commit до отдельного preview. При любой ошибке не заменяется ни один файл. Сохраните канонические CSV перед импортом: именно они, а не XLSX или mapping, являются идентичностью последующих preview/commit/reconcile/export. При потере ответа повторяйте те же канонические файлы. 403/404 очищают комплект; 422 — исправьте заголовки/формат/область; 409 — сверяйте ранее подтверждённые исходники. Только искусственные данные, без клиентов, истории, платежей и production approval. Откат — выключить флаг или revert кода, без миграций.
