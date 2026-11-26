# Полный вывод робототехнического домена из «БоксРитм» (#165)

**Статус:** HTTP runtime cutover выполнен в `main`; legacy SQL, исторический draft-контракт, хранение вложений и production cutover ещё требуют approval #119. «БоксРитм» — автомобильный продукт.

## Фактическая граница

| Слой | Изменение | Следующее требование |
|---|---|---|
| UI | Автомобильная главная, кабинеты, навигация; robot UI и SDK удалены | Не восстанавливать старые React routes и HTTP вызовы |
| ASGI | Рабочая точка входа `roboport_api.main:app` больше не монтирует robot ticket, estimate, inventory, catalog, guest claims, legacy staff queue/audit | Регрессия `tests/bootstrap/test_auto_runtime_retired_legacy_routes.py` |
| Historical HTTP | Тестовый harness `tests/legacy_app.py` сохраняет синтетические негативные тесты старой модели; не входит в deployable API package | Убрать после архивирования и data reconciliation, никогда не запускать как production app |
| Контракт | `contracts/openapi.yaml` и `contracts/operation_ids.json` — исторический draft | Отдельно архивировать; заменить публичным автомобильным контрактом после review |
| SQL / файлы | Legacy таблицы, ACL, вложения и миграции сохранены | Инвентаризация всех FK/object keys, backup, restore, сверка, согласованное удаление |

## Read-only dependency inventory перед decommission (#165, current-dev)

После миграций на **одноразовом синтетическом PostgreSQL 17** можно получить JSON со списком объектов схемы `public`: таблицы/представления/последовательности, внешние ключи между таблицами и пользовательские триггеры. Инструмент не читает строки таблиц, владельцев клиентов, VIN, object keys или содержимое вложений:

```bash
ROBOport_SYNTHETIC_AUTO_API=1 ROBOport_DATABASE_URL='postgresql+psycopg://roboport:roboport@127.0.0.1:5432/roboport' \
  python scripts/inspect_legacy_db_surface.py > /tmp/legacy-db-schema.json
PYTHONPATH=apps/api python -m pytest -q tests/bootstrap/test_legacy_db_surface_policy.py
PYTHONPATH=apps/api python -m pytest -q tests/integration/test_pg_legacy_db_surface.py
```

Утилита требует явный loopback PostgreSQL, включённый synthetic flag и выполняет чтение внутри транзакции `REPEATABLE READ READ ONLY`. Она не следует удалённым PostgreSQL hosts/service/hostaddr и при ошибке возвращает `BLOCKED` без печати DSN. В JSON `legacy-or-unclassified` **не значит «можно удалить»**: это консервативный список для ручного анализа; `shared-review` отмечает только явно перечисленные инфраструктурные сущности, а `auto_*` — автомобильные объекты. `crossBoundary` выделяет FK через эти группы. Внутренние системные триггеры/FK-реализация не перечисляются; пользовательские триггеры показываются по имени. Это снимок **только** схемы `public`, без внешних схем, функций без привязки к триггерам, row counts, ACL, данных, MinIO и runtime API; для полного cutover необходимы отдельные инвентаризация файлов/владельцев, доказанное сопоставление истории, восстановление, RPO/RTO и approvals.

Отчёт всегда содержит `releaseAllowed: false`, `dropAllowed: false`. Он никогда не является командой DROP, доказательством полного переноса или разрешением production. Перед финальной миграцией требуется независимая классификация каждой строки, точный backup/restore и legal/security/ops gate #119.

## PostgreSQL 17 legacy-вложения: полный metadata restore-check (#165, current-dev)

В `.github/workflows/ci.yml` встроен отдельный тест реального `pg_dump -Fc` → `pg_restore` **того же самого** синтетического стенда: перед dump создаются `customer` → `service_ticket` → две записи `attachment` (`READY/CLEAR` и `FAILED/QUARANTINED`) и одна `attachment_upload_dedupe`. Внешний JSON manifest с UUID и SHA-256 полного графа остаётся вне dump. После restore на обеих базах отдельно сверяются идентичность исходного клиента/заявки/автора, ключ и SHA metadata, размеры, MIME, upload/quarantine состояния, даты и idempotency replay lineage. Пустые/неполные графы или несовпадающий SHA дают `BLOCKED`; попытки перепривязать вложение к чужой заявке и снять карантин отвергаются PostgreSQL-триггером в интеграционном тесте.

```bash
PYTHONPATH=apps/api python -m pytest -q tests/contract/test_auto_legacy_attachment_restore_probe.py
PYTHONPATH=apps/api python -m pytest -q tests/integration/test_pg_legacy_attachment_restore_probe.py
# Только в одноразовом CI PG17, с точным ROBOport_DATABASE_URL и PG env без переопределений:
PYTHONPATH="apps/api:tests/integration" python -m scripts.auto_legacy_attachment_restore_probe seed --manifest /tmp/roboport-legacy-attachment-restore.json
# Между seed и verify выполнить pg_dump / pg_restore (см. .github/workflows/ci.yml).
PYTHONPATH="apps/api:tests/integration" python -m scripts.auto_legacy_attachment_restore_probe verify-source --manifest /tmp/roboport-legacy-attachment-restore.json
PYTHONPATH="apps/api:tests/integration" python -m scripts.auto_legacy_attachment_restore_probe verify-clone --manifest /tmp/roboport-legacy-attachment-restore.json
```

**Граница доказательства:** у записей `attachment` только синтетические метаданные, их ключи **не соответствуют загруженным объектам MinIO**. Проверка **не** доказывает существование файла, совпадение его реальных байтов и SHA, восстановление/карантин MinIO, сроки хранения и право на реальную историю. Не объявлять этот CI-тест «полным backup вложений». Для закрытия #165 нужен отдельно санкционированный private-object inventory, byte/hash reconciliation и восстановление object storage, плюс независимые approvals #119. Утилита не исполняет `DROP`/`DELETE` и не предоставляет клиентский API.

## Физическое восстановление legacy-вложений MinIO + PostgreSQL 17 (#165, current-dev)

Workflow `.github/workflows/minio-dev.yml` запускает `tests/integration/test_pg_minio_legacy_restore.py` на одноразовых PostgreSQL 17 и MinIO с loopback endpoints и одноразовыми app credentials. Это отдельное доказательство от metadata-only проверки выше. Восстанавливаются **два** синтетических объекта: READY/CLEAR в documents и FAILED/QUARANTINED в quarantine. SQL-граф содержит исходного автора, клиента, заявку, оба attachment и связь idempotency. Проверяются реальные bytes, размер, MIME и SHA-256, отсутствие копии в неправильном бакете. Обычный FileStorage не выдаёт карантинный объект.

1. Перед backup прочитать граф и сверить оба объекта с SQL; собрать временный ZIP (manifest и два файла), привязать его к SHA-256 графа.
2. Выполнить реальный `pg_dump -Fc` с последующим `pg_restore --exit-on-error` в отдельную БД `roboport_restored`; проверить восстановленный SQL-граф в read-only транзакции.
3. Проверить manifest, допустимый набор файлов, размеры и SHA-256 **до записи**; восстановить только в два пустых приватных restore-бакета, отличных от source. Используется отдельный временный CI-only restore-пользователь: source GET/LIST, restore GET/PUT/DELETE/LIST. App-пользователь не получает restore policy; прямое чтение, удаление и перечисление карантина для него запрещены. Проверить запрет анонимного доступа.
4. Повторно сверить bytes, MIME, SHA-256 и размещение с восстановленной SQL-метаинформацией. Некорректный архив не должен записать ни одного объекта, повторный restore в непустое назначение отвергается. Негативные сценарии (missing / corrupted / wrong bucket) выполняются только в disposable restore-бакетах; в конце проверяется неизменность исходного графа и source-объектов.

Команда теста, **только внутри подготовленного MinIO workflow**:

```bash
PYTHONPATH=apps/api ROBOport_LEGACY_RESTORE_CI=1 python -m pytest -q tests/integration/test_pg_minio_legacy_restore.py
```

Не запускать на локальной рабочей базе и реальных бакетах. Требуются disposable credentials, loopback DSN, ID одноразового postgres-контейнера, отдельные пустые restore-бакеты. Архив и дамп временные, не публикуются как artifacts. MinIO workflow запускается на PR и push в main для затронутых файлов. Для production по-прежнему необходимы реальный полный inventory/reconciliation (не только два контрольных объекта), правовое основание хранения, проверка миграции истории и независимые approvals #119, RPO/RTO и отдельная финальная миграция. Этот тест не выдаёт разрешений на удаление или релиз.

## Гейты для полного закрытия

1. **HTTP:** у всех robot URL всегда 404, даже с тестовыми переключателями и поддельными заголовками; нет robot пути в runtime OpenAPI; автомобильные CRUD/ACL и вход работают.
2. **Контракт:** новый versioned automotive public API, отсутствие legacy draft из генерируемого клиентского SDK, проверка совместимости и документация.
3. **Данные:** инвентаризация robot tables, FK, триггеров, object keys, владельцев и хранения. Синтетические и разрешённые по правовому основанию архивные наборы сверены count+hash, успешно выполнен PostgreSQL 17 dump/restore, записаны RPO/RTO и откат.
4. **Security/privacy/legal/ops:** независимые подписанные approvals по #119, negative BOLA/revocation/ownership-history tests; VIN не создаёт права читать прошлую историю.
5. **Decommission:** только после пп. 2–4 финальная Alembic миграция для удаления старых таблиц и файлов по срокам retention, с журналом операций и rollback; до этого SQL DROP запрещён.

## Проверяемые команды

```bash
PYTHONPATH=apps/api python -m pytest -q tests/bootstrap/test_auto_runtime_retired_legacy_routes.py
PYTHONPATH=apps/api python -m pytest -q tests/bootstrap tests/contract
PYTHONPATH=apps/api python -m pytest -q tests/integration  # disposable PostgreSQL 17
```

В Docker/uvicorn допустима только рабочая точка входа `roboport_api.main:app`; тестовый архив не должен импортироваться ею. Пять обязательных CI jobs должны пройти как на PR, так и после merge. Revert изменений HTTP не затрагивает данные. Отдельного production-rollback этот checkpoint не заменяет.

См. [архив](LEGACY_ARCHIVE.md), [продукт](PRODUCT.md) и [архитектуру](ARCHITECTURE.md).

## Полная статическая инвентаризация HTTP-маршрутов с CI-gate (#165)

**Проблема и решение:** старый read-only сканер `scripts/inventory_legacy_routes.py`
перечислял только `@router.get("/literal")` и пропускал реальные пути
вида `PREFIX + "/export"`. Это могло скрыть восстановленный legacy URL
при инвентаризации. Проверяемая версия собирает пути из безопасных
**статических** строковых констант модуля, конкатенаций и literal f-string,
учитывает `APIRouter(prefix=...)`, унаследованное
`include_in_schema=False` и императивные `add_api_route`.
Ни код приложения, ни SQL, ни переменные окружения при сканировании
не выполняются и не читаются.

```bash
python scripts/inventory_legacy_routes.py --legacy-only > /tmp/legacy-http-paths.json
python scripts/inventory_legacy_routes.py --check
PYTHONPATH=apps/api python -m pytest -q tests/bootstrap/test_legacy_route_inventory.py
```

`--check` выполняется **на каждом PR и на main в обязательном docs job**.
Если выражение пути/префикса не может быть достоверно вычислено статически,
сборка FAIL: возвращается список только `source`/`line`, без дампа
содержимого кода, cookie или данных. Непонятный маршрут **не исчезает**
из JSON-инвентаризации: это `unresolved=true`,
`review=path-unresolved`. Проверять новые правила разрешения путей
нужно только тестами, никогда `eval`/импортом произвольного модуля.
Поля `legacy_candidate` — консервативный сигнал на ручной анализ,
включающий старые unmounted Python-модули, а не доказательство, что
путь обслуживается рабочим ASGI.

**Граница готовности:** статический список не доказывает монтирование,
доступность или отсутствие обхода авторизации. Отдельные runtime
TestClient-негативные проверки и OpenAPI-gate выше остаются обязательны.
Это закрывает *дефект полноты технического route inventory*, но **не**
закрывает #165: legacy SQL/attachment retention, настоящее восстановление
и миграция архивов, одобренные права на данные, #119 и production cutover
по-прежнему блокируют деструктивный decommission. Откат — revert этого
статического валидатора и его CI/тестов; объекты БД, файлы и
функционирующие маршруты не изменяются.


## Инвентаризация исторического OpenAPI draft (#165, current-dev)

Поскольку `contracts/openapi.yaml` — **исторический draft**, его нельзя выдавать за runtime-контракт или публиковать в клиентский SDK. Read-only сканер `scripts/inventory_legacy_routes.py` теперь включает все 46 статических path/method операций этого файла в общий JSON с `contract_status=historical-draft-not-runtime`, `operation_id`, источником и консервативной меткой legacy. Сканер не загружает YAML-теги, не разрешает ссылки, не импортирует приложение и не обращается к сети. Он проверяет все элементы `servers` независимо от порядка `description`/`url`, принимает quoted path keys и trailing comments у методов, а неизвестные ключи, дубли и неполный разбор переводит в unresolved и блокирует `--check` вместо наследования предыдущего пути/метода. Дополнительно парсер требует точную глубину отступов серверных полей, отклоняет все неподдерживаемые YAML escapes в path keys и case-variant HTTP methods, а также fail-closed обрабатывает path/operation-level `servers`, `$ref` и inline method mappings. Root-level quoted/merge/anchored sections, дубли `paths`/`servers`, YAML sequence markers без разделителя и operation-level quoted/merge keys также блокируют inventory; `callbacks` не пропускаются, поскольку содержат вложенные Path Items. Draft title/version принимаются только из единственного root `info` object; одноимённые поля в других разделах не могут подменить маркеры. Парсер также отклоняет unsupported explicit/root YAML key syntax и path keys с пробелами/query, чтобы не нормализовать их в ложный маршрут. Percent-encoded path keys не классифицируются автоматически: они переводятся в unresolved до ручной нормализации. Root keys с префиксами YAML anchor/tag/alias и много-документные маркеры `---`/`...` тоже не принимаются ограниченным парсером. Маркеры с trailing comments также считаются разделителями документов и блокируют `--check`. OpenAPI root `webhooks` тоже блокирует `--check`, пока его операции не включены в отдельную инвентаризацию. Обязательный `--check` падает, если исчез раздел paths, путь/метод нельзя разобрать, нарушена уникальность path+method, исчезли draft title/version или placeholder `.invalid` server. Это защищает от случайной публикации старого draft, но **не** доказывает, что все operationId/SDK references удалены из внешних сборок, и не заменяет новый reviewed automotive OpenAPI #131.

```bash
python scripts/inventory_legacy_routes.py --legacy-only > /tmp/legacy-api-inventory.json
python scripts/inventory_legacy_routes.py --check
PYTHONPATH=apps/api python -m pytest -q tests/bootstrap/test_legacy_route_inventory.py
```

Изменение read-only и обратимо через revert скрипта/тестов/этого раздела. Контракт, API runtime, DDL, данные и миграции не изменяются; `dropAllowed=false`, `releaseAllowed=false`.


## Source-level inventory исторических Alembic DDL (#165)

В дополнение к metadata-only снимку PostgreSQL \`public\` выполнена статическая сверка исходников ранней схемы \`apps/api/alembic/versions/0001_core.py\`–\`0020_guest_proof.py\`. Это **20 ревизий legacy-era**, не те пустые файлы \`db/legacy_drafts/001–006.sql\` (они имеют нулевой размер и никогда не должны исполняться).

Срез по SQL \`CREATE TABLE/FUNCTION/TRIGGER/INDEX\` в этих ревизиях:

- **46 уникальных таблиц**;
- **29 функций**, **40 пользовательских триггеров** (включая 10 экземпляров, создаваемых в циклах миграций), **8 индексов**;
- несколько ревизий не создают таблиц, но меняют ограничения/поведение существующих объектов (например, quote race, estimate ACL и ticket event actor).

| Revision | Создаваемые таблицы | Functions / triggers / indexes |
|---|---|---:|
| \`0001_core\` | \`customer\`, \`robot_device\`, \`service_ticket\`, \`ticket_state_event\`, \`audit_event\` | 2 / 2 / 1 |
| \`0002_estimate_stock\` | \`estimate\`, \`estimate_line\`, \`stock_lot\` | 2 / 2 / 0 |
| \`0003_idempotency\` | \`idempotency_request\` | 1 / 1 / 1 |
| \`0004_approval\` | \`estimate_approval\` | 2 / 2 / 0 |
| \`0005_quote_race\` | нет — изменение существующей модели | 1 / 1 / 0 |
| \`0006_local_identity\` | \`app_user\`, \`local_credential\`, \`user_session\`, \`account_customer_link\` | 1 / 1 / 1 |
| \`0007_auth_throttle\` | \`auth_login_throttle\` | 0 / 0 / 0 |
| \`0008_authorization\` | \`role\`, \`user_role\`, \`acl_entry\`, \`authorization_audit\` | 1 / 1 / 2 |
| \`0009_attachments\` | \`attachment\`, \`attachment_upload_dedupe\` | 1 / 1 / 1 |
| \`0010_estimate_acl\` | нет — изменение ACL существующих объектов | 0 / 0 / 0 |
| \`0011_ticket_transition_actor\` | нет — guard для существующего \`ticket_state_event\` | 1 / 1 / 0 |
| \`0012_safety_review\` | \`ticket_safety_review\` | 1 / 1 / 0 |
| \`0013_ticket_idempotency\` | \`ticket_mutation_receipt\` | 0 / 0 / 0 |
| \`0014_estimate_decision\` | \`estimate_decision_receipt\`, \`estimate_staff_receipt\` | 1 / 2 / 0 |
| \`0015_catalog\` | \`catalog_model\`, \`service_capability\`, \`capability_approval\`, \`part_compatibility\`, \`part_compatibility_approval\`, \`catalog_review_receipt\`, \`catalog_audit\` | 6 / 4 / 0 |
| \`0016_inventory\` | \`inventory_part\`, \`stock_reservation\`, \`stock_movement\`, \`inventory_receipt\` | 3 / 2 / 0 |
| \`0017_outbox\` | \`notification_outbox\`, \`synthetic_notification_delivery\` | 3 / 5 / 1 |
| \`0018_customer_preferences\` | \`user_session_csrf_rotation\`, \`customer_notification_preference\`, \`customer_preference_audit\` | 1 / 1 / 0 |
| \`0019_oidc_identity\` | \`oidc_identity\` | 1 / 1 / 0 |
| \`0020_guest_proof\` | \`guest_ticket_claim\`, \`guest_intake_request\`, \`guest_intake_rate\`, \`guest_claim_audit\` | 1 / 2 / 1 |

**Disposition пока не назначен автоматически.** Сверка текущего automotive runtime выявила общие зависимости ранней схемы: \`app_user\`, \`local_credential\`, \`user_session\`, \`user_session_csrf_rotation\`, \`auth_login_throttle\`, \`role\`, \`user_role\`, \`customer\`, \`account_customer_link\`, \`oidc_identity\`. Они классифицируются как \`shared-review\` в \`scripts/inspect_legacy_db_surface.py\`, поскольку используются auth/session, staff/organization authority, vehicle claim и OIDC flows (в частности \`auth_http.py\`, \`session_service.py\`, \`auto_manager_authority.py\`, \`auto_org_authority.py\`, \`auto_vehicle_claim.py\`, \`oidc_http.py\`). \`shared-review\` не означает «можно удалить» и не является финальным ownership approval. Остальные не-\`auto_*\` отношения остаются \`legacy-or-unclassified\`; это тоже не означает «можно удалить».

Эта таблица фиксирует только статические DDL declarations в ревизиях 0001–0020. Она **не** доказывает, что live PostgreSQL содержит ровно тот же набор объектов, не описывает все последующие \`ALTER\` к legacy-таблицам, внешние схемы, grants, реальные row counts, object keys, владельцев, retention или legal basis. Для runtime-снимка остаётся обязательным одноразовый synthetic PG17 \`inspect_legacy_db_surface.py\`; для каждого реального объекта перед финальным decommission потребуется независимое решение \`retain / migrate / archive / remove\` с owner, evidence, backup/restore и rollback.

До такого решения запрещены \`DROP\`/\`DELETE\` и production cutover. \`dropAllowed=false\`, \`releaseAllowed=false\`.


### Изоляция доступа к карантину и восстановлению (#165/#119)

`infra/dev/minio-app-policy.json` ограничивает quarantine до PUT и GetBucketLocation и явно запрещает GET/DELETE/LIST. `infra/dev/minio-restore-policy.json` выдаётся только отдельному временно созданному пользователю на одноразовом стенде: read-only source, read/write только изолированных restore bucket. Корневые credentials не передаются Python; seed использует обычное приложение, сверка — отдельный restore principal. Тесты реального S3 проверяют AccessDenied для app GET/HEAD/LIST/DELETE карантина, app-доступа к restore bucket и restore PUT/DELETE source, а также сохранение исходных bytes/hash. `minio-dev` запускается на PR и main при изменениях политики/адаптера/интеграции. См. [локальный runbook](../../infra/dev/README.md). PUT карантина не является неизменяемым WORM, не доказаны production retention, AV и роль реального оператора; #119/#165 остаются открытыми. Откат только через согласованное восстановление dev policy и повторную отрицательную проверку, не через расширение production-доступа.

## Инвентаризация шести архивных PR (#165, 25.11.2026)

Контрольная точка сравнения: `9b992b86ba176cfcb1da8e2cf0c8528671901fc7` (main после #595). Проверены списки файлов шести закрытых без merge PR и полные Git trees их head и контрольной точки. Сравниваются только пути из changed files каждого PR: одинаковый blob SHA / изменённый blob SHA / путь отсутствует в main. Один путь может входить в несколько PR; сумма по строкам не является количеством уникальных файлов. Совпадение blob не доказывает доступность функции в runtime, отличие не означает потерю функции. Это source-level проверка, не разрешение на удаление веток или данных.

| Архивный PR | Head | Одинаковые / изменённые / отсутствующие | Решение на текущем этапе |
|---|---|---:|---|
| [#1](https://github.com/askarahodov/roboport/pull/1) | `59686f8c4c498b627a285411fb053cd34ab9ce27` | 1 / 58 / 0 | Не переносить старое ТЗ, UI и robot OpenAPI поверх актуальных automotive ADR. |
| [#45](https://github.com/askarahodov/roboport/pull/45) | `303a090b99672c3dc7ed779e33d25c919d62d598` | 3 / 9 / 0 | Общий механизм подписанных курсоров уже существует; старые ticket routes не возвращать. |
| [#46](https://github.com/askarahodov/roboport/pull/46) | `18cee0212ddc9a7bb8ec387d0e14e391501c5e13` | 2 / 4 / 0 | Dev Compose сохранён, актуальные IAM/restore и CI использовать вместо старой политики. |
| [#47](https://github.com/askarahodov/roboport/pull/47) | `4abfa1c81e4adab6507c888fb7314344ac12f0a0` | 2 / 3 / 0 | FileStorage и requirements-dev совпадают по blob SHA; не дублировать адаптер. |
| [#59](https://github.com/askarahodov/roboport/pull/59) | `b98b4ccc620df5cf1b09961af34713efe0f7084a` | 0 / 2 / 3 | Не вводить вторую legacy Alembic 0015; сравнивать инварианты истории владельца отдельно. |
| [#60](https://github.com/askarahodov/roboport/pull/60) | `98ae7a5ffc66bb6e74a033a8c81206c1648f4d86` | 0 / 8 / 5 | Не переносить robot model/dock/variant в automotive SKU и OEM. |

### Проверенные технические механизмы и оставшиеся вопросы

- **#45:** `keyset_cursor.py` использует HMAC SHA-256, constant-time comparison и привязку к session token; исходный `test_signed_keyset_cursor.py` имеет тот же blob SHA и проверяет tamper, noncanonical encoding, неверные типы и подписанные некорректные payload. Для новых API остаётся отдельная проверка scope, живых ролей и отказов доступа. Исторические ticket ACL не являются автомобильным ACL.
- **#46/#47:** `infra/dev/compose.minio.yml`, `infra/dev/.env.example` и `storage.py` сохранены. Актуальный `minio-dev.yml` содержит проверки private bucket, S3 policy, FileStorage, PostgreSQL attachment HTTP и isolated restore. Наличие workflow не доказывает его запуск на этом PR; отдельный MinIO gate и provenance/production retention остаются обязательными.
- **#59:** отсутствуют прежние `0015_device_ownership.py`, `compose.postgres.yml` и `test_pg_device_ownership.py`. Автомобильный guard `0133_auto_vehicle_link_guard.py` и `test_pg_auto_vehicle_link_guard.py` защищают неизменяемость идентичности/верификации и необратимость отзыва связи. Это не доказательство полного переноса всех требований legacy ownership, интервалов, единственного активного владельца и конкурентных переходов. Не вводить правило единственности для auto link без отдельной проверки модели и допуска нескольких законных связей.
- **#60:** отсутствуют три файла старого `modules/catalog`, `CATALOG_COMPATIBILITY_GATES.md` и `test_pg_catalog_compatibility.py`. Актуальная `0124_auto_catalog.py` реализует локальный каталог, цены, provenance и неизменяемые события; `auto_parts_provider.py` и `auto_parts_boundary.py` отделяют применяемость от кроссов. В частности, cross lookup не может сам дать VERIFIED. Старый двухэтапный robot approval не равен automotive OEM evidence. Проверка реального лицензированного источника и applicable approvals остаётся в #111/#115/#119.
- **#1:** сохранено наличие всех путей, но почти все blob изменились. Исторический draft и legacy SQL не являются целевой архитектурой; отдельные проверки runtime, migration/recovery и публичного OpenAPI описаны выше.

Воспроизводимость: для каждого PR взять список `pulls/N/files` (с пагинацией) и head SHA, запросить `git/trees/SHA?recursive=1` для обеих ревизий, убедиться в отсутствии truncation и сравнить blob SHA по каждому пути. Для отсутствующих путей и изменённых security-sensitive файлов дополнительно сравнивать бизнес-инварианты, а не только имена.

**Граница:** выявлены направления для дальнейшей предметной проверки, а не доказанные дефекты или полная функциональная эквивалентность. Никаких cherry-pick, удаления старых refs, DDL, production cutover или реальных данных. Откат документации — revert. #165 остаётся OPEN; `dropAllowed=false`, `releaseAllowed=false`.


### Зависимости за границей public (#165, current-dev)

Read-only `scripts/inspect_legacy_db_surface.py` сохраняет список public relations и дополнительно включает FK с любым концом в public (fromSchema/toSchema), зависимости представлений и materialized views на отношения из pg_rewrite/pg_depend (без self edges и повторов на уровне колонок), schema/name функций пользовательских триггеров public. CrossBoundary учитывает различие схем и классификации. SQL не читает содержимое строк, определения представлений или тела функций; `releaseAllowed=false`, `dropAllowed=false`. PG17 test `test_inventory_keeps_cross_schema_edges_and_trigger_functions_without_data` создаёт уникальные synthetic fixtures внутри транзакции, проверяет обе стороны view dependencies, FK, trigger identity, повторяемость и отсутствие содержимого строки, откатывает fixture DDL. Существующий тест отдельно подтверждает реальный read-only transaction. Это расширение карты зависимостей, не полный граф pg_depend (динамический SQL, тела функций, внешние клиенты и реальные retention не доказаны) и не разрешение на удаление. Откат — revert скрипта/теста/документации; production #119 сохраняется.


### Полная проверка исторического HTTP-инвентаря (#165)

`test_every_archived_robot_operation_is_unreachable_in_current_runtime` извлекает статические method/path из frozen `tests/legacy_app.py`, `guest_claim_http.py` и исторического `contracts/openapi.yaml` без импорта архивного приложения. Исключены только общие health/meta/preferences и auth namespace draft; остальные исторические операции проверяются их собственным HTTP-методом и синтетическим UUID через отдельный процесс текущего ASGI. Требуются 404 без redirect и без spoofed proxy header, отсутствие путей в runtime OpenAPI, положительные контрольные health 200 и automotive 403 без DSN. Неизвестный путь в архивном источнике или unresolved inventory блокирует тест. Это regression check известных исходников, не полный security audit динамических маршрутов, не production ingress test и не разрешение на DROP. Runtime и DDL не меняются; откат revert теста и документации.

Дополнение по review P1/P2: проверка использует ASGI `route.matches` (включая lazy routers), а не только HTTP 404. Отрицательный контроль намеренно подключает скрытый архивный router через вложенный APIRouter в отдельном приложении; регистрация должна обнаруживаться независимо от DSN. В инвентарь добавлен `auth_http.py`, исключения общих операций перечислены явно; staff queue/audit обязательны.


## Расширенная карта каталогов и границ зависимостей (#165, current-dev)

Единый read-only отчёт `inspect_legacy_db_surface.py` дополнен объектами и прямыми рёбрами `pg_depend` для отношений (включая индексы и партиции), типов, функций/процедур/агрегатов, ограничений, default, rewrite rules, пользовательских триггеров и RLS policies. Включены объекты public и непосредственно связанные внешние объекты разрешённых классов. Идентичность — schema/kind/name/ownerRelation/argumentTypes, а для зависимостей колонок — ordinal; OID не выводятся. Перегрузки функций различаются по типам входных аргументов. `inheritance` содержит связи родитель/партиция, включая переходы между схемами; `sequenceOwnership` отделяет OWNED BY и identity от использования sequence в default; `rowSecurity` показывает включение/force RLS и метаданные политики (команда, permissive/restrictive, число ролей и PUBLIC), **не** effective permissions. Обычные FK/view/trigger поля отчёта сохранены.

Отчёт не декомпилирует тела функций, SQL defaults, view/policy expressions, не читает бизнес-строки, пароли, ключи или имена ролей. Системные схемы в catalog graph исключены. `catalogCoverage.completeDependencyGraph=false` явно фиксирует ограничения: динамический SQL, строковые тела функций, зависимости, не записанные в pg_depend, транзитивные внешние рёбра, внешние приложения, extension/прочие классы каталогов, эффективные grants/ACL, данные и object storage не доказаны. Префикс auto_ не разрешает удаление; итоговый отчёт всегда inventory-only, releaseAllowed=false/dropAllowed=false.

PG17: одноразовые fixtures в одной откатываемой транзакции проверяют перегрузки функций, SQL-standard body с cross-schema dependency, column default и policy edges, partition, sequence OWNED BY/identity, RLS flags и отсутствие значения-маркера в отчёте. Существующий тест подтверждает read-only и repeatable metadata. Команды прежние; для production по-прежнему обязательны реальный inventory/retention, полный reconciliation/restore, оценка effective grants и утверждённые RPO/RTO, #119 и отдельное согласование destructive decommission. Откат только revert скрипта, тестов и документации; DDL/бизнес-данные не меняются.
