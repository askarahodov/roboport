# Полный вывод робототехнического домена из Airmatik (#165)

**Статус:** HTTP runtime cutover выполнен в отдельной ветке. Legacy SQL, исторический draft-контракт, хранение вложений и production cutover ещё требуют approval #119. Airmatik — только автомобильный продукт.

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
3. Проверить manifest, допустимый набор файлов, размеры и SHA-256 **до записи**; восстановить только в два пустых приватных restore-бакета, отличных от source. Используются отдельные CI-only разрешения, не меняющие штатный app policy. Проверить запрет анонимного доступа.
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