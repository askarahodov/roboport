# Локальный MinIO — Issue #28

Только синтетические файлы. **Не production, не публичный сервис.**
Исходный официальный образ Quay возвращает `unauthorized` (подтверждено CI). Вместо него для **изолированного dev** используется мультиархитектурный образ `tobi312/minio`, привязанный к неизменяемому OCI digest `sha256:e2226dea4b9aef896db02f7396102d48eb58cd339d930332e3d8bdac80012a78` (релиз MinIO 2025-09-07). Это **сторонний републикатор**, не официальный источник: перед production обязательно заменить на проверенный внутренний образ с аттестацией происхождения. Не использовать `latest`.

## Старт

1. Установите Docker Compose v2 и MinIO Client (`mc`) вне репозитория.
2. Скопируйте `infra/dev/.env.example` в `infra/dev/.env.local`, локально заполните случайными `MINIO_ROOT_USER` и `MINIO_ROOT_PASSWORD` (не коммитить).
3. `docker compose -f infra/dev/compose.minio.yml up -d`
4. `docker compose -f infra/dev/compose.minio.yml ps` — порт API `127.0.0.1:9000`, консоль `127.0.0.1:9001`.
5. `set -a; . infra/dev/.env.local; set +a` (только доверенная локальная машина).
6. `mc alias set rp-dev http://127.0.0.1:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD"`
7. `mc mb -p rp-dev/roboport-documents-dev` и `mc mb -p rp-dev/roboport-quarantine-dev`
8. `mc anonymous set none rp-dev/roboport-documents-dev` и `mc anonymous set none rp-dev/roboport-quarantine-dev`

## Отдельный пользователь приложения

Сгенерируйте и сохраните **вне Git** переменные `MINIO_APP_ACCESS_KEY` и `MINIO_APP_SECRET_KEY`.
Корневые ключи **не** передавать backend.

```bash
mc admin user add rp-dev "$MINIO_APP_ACCESS_KEY" "$MINIO_APP_SECRET_KEY"
mc admin policy create rp-dev roboport-app-readwrite infra/dev/minio-app-policy.json
mc admin policy attach rp-dev roboport-app-readwrite --user "$MINIO_APP_ACCESS_KEY"
mc alias set rp-app http://127.0.0.1:9000 "$MINIO_APP_ACCESS_KEY" "$MINIO_APP_SECRET_KEY"
mc ls rp-app/roboport-documents-dev
```

Права приложения: GET/PUT/DELETE/LIST в documents, только PUT в quarantine (плюс GetBucketLocation для обоих). Прямые GET/HEAD/DELETE/LIST карантина явно запрещены на уровне S3 IAM. Обычный FileStorage может переместить объект в карантин, но не читать или удалять его оттуда. PUT сам по себе не гарантирует WORM: защита от перезаписи известного ключа и production retention требуют отдельного решения #119.
Эта S3 policy **не заменяет** доменный ACL: backend обязан повторно проверить сессию,
роль, связь с заявкой и состояние quarantine перед любым чтением/скачиванием.

## Ручной интеграционный smoke-test (только синтетика)

```bash
printf 'synthetic-robot-file-1\n' > /tmp/rp-test-1.txt
printf 'synthetic-robot-file-2\n' > /tmp/rp-test-2.txt
mc cp /tmp/rp-test-1.txt rp-app/roboport-documents-dev/test-1.txt
mc cp /tmp/rp-test-2.txt rp-app/roboport-documents-dev/test-2.txt
mc cat rp-app/roboport-documents-dev/test-1.txt
mc cat rp-app/roboport-documents-dev/test-2.txt
curl -i http://127.0.0.1:9000/roboport-documents-dev/test-1.txt
curl -i 'http://127.0.0.1:9000/roboport-documents-dev/?list-type=2'
mc rm rp-app/roboport-documents-dev/test-1.txt
mc rm rp-app/roboport-documents-dev/test-2.txt
```

Оба анонимных `curl` обязаны вернуть **403**, не 200.
Ни quarantine, ни чужой файл нельзя отдавать напрямую через HTTP; эти проверки
должны быть повторены через backend после появления FileStorage (#25).

## Остановка и полный сброс синтетики

```bash
docker compose -f infra/dev/compose.minio.yml down
docker compose -f infra/dev/compose.minio.yml down -v
```

`down -v` безвозвратно удаляет локальный том MinIO. Только для disposable dev.
Секреты из `.env.local` удалить отдельно локально.

**Статус:** конфигурация подготовлена, запуск/отрицательные проверки **не подтверждены**:
в текущем CI/терминале отсутствует Docker. #28 до независимого TESTER остаётся BLOCKED.


## Резервная копия и восстановление dev-bucket (только синтетика)

Архивирование карантина **не выполняется от имени приложения**. На одноразовом доверенном dev-стенде отдельный временный субъект с `infra/dev/minio-restore-policy.json` имеет только GET/LIST на двух исходных bucket и GET/PUT/DELETE/LIST на двух **отдельных** restore-bucket. Не добавлять эту policy пользователю приложения. Создание субъектов и бакетов выполняет оператор; ключи хранятся вне Git, root не передаётся backend.

При наличии выданных оператором временных restore credentials и пустых приватных `roboport-documents-restore-dev` / `roboport-quarantine-restore-dev`:

```bash
mc alias set rp-restore http://127.0.0.1:9000 "$MINIO_RESTORE_ACCESS_KEY" "$MINIO_RESTORE_SECRET_KEY"
mkdir -p /tmp/roboport-minio-dev-backup
mc mirror rp-restore/roboport-documents-dev /tmp/roboport-minio-dev-backup/documents
mc mirror rp-restore/roboport-quarantine-dev /tmp/roboport-minio-dev-backup/quarantine
# Только в пустые изолированные restore-bucket после сверки PostgreSQL metadata:
mc mirror /tmp/roboport-minio-dev-backup/documents rp-restore/roboport-documents-restore-dev
mc mirror /tmp/roboport-minio-dev-backup/quarantine rp-restore/roboport-quarantine-restore-dev
```

Это локальная копия синтетики, не production backup. Полный CI drill сверяет граф PG17, bytes/MIME/SHA-256 и пустое назначение; проверяет запрет чтения/удаления карантина app-пользователем, запрет доступа приложения к restore-bucket и запрет мутаций source restore-пользователем. После теста удалить локальный архив и отозвать временный субъект. Для действующих dev-стендов заменить старую policy через доверенного оператора и повторить отрицательные проверки; не выдавать приложению старые широкие права ради восстановления.

Отдельная служебная функция `storage_reconcile.purge_old_orphans` удаляет
объекты без записи `attachment` старше часа из bucket документов. Quarantine
не обрабатывается ей, чтобы не нарушить forensic evidence. Нельзя вызывать
эту функцию из пользовательского HTTP.
