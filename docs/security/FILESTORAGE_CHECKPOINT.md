# Checkpoint RBAC/ACL + FileStorage (Issues #27, #28, #25)

## Что проверено на настоящих PostgreSQL 17 и MinIO

- Alembic 0008/0009, включая downgrade/re-upgrade и SQL-ограничения.
- Роли CLIENT/INTAKE/ENGINEER/MANAGER и объектные ACL, отсутствие сквозного доступа ADMIN.
- Отзыв роли, сессии, ссылки customer и назначений; повторная попытка не возвращает доступ.
- Синтетические вложения: PUT/GET/DELETE/QUARANTINE, проверка SHA-256, MIME allowlist, лимит 8 MiB.
- Приватные bucket, анонимные LIST/GET запрещены, приложение работает без root MinIO.
- `GET /api/v1/dev/attachments/{attachment_id}/content`: проверка действующих ролей, связи с исходной заявкой, upload_state, quarantine_state; данные отдаются только через backend.
- `POST /api/v1/dev/tickets/{ticket_id}/attachments`: требуется локальная сессия, same-origin и CSRF, отказ при недействительной ACL и SAFETY_HOLD/QC_FAILED.
- При неудачном DB commit сделана попытка удалить записанный объект; отдельный служебный reconciler очищает старые (>1 часа) непривязанные документы.

## Команды CI

Полный набор GitHub Actions: `.github/workflows/ci.yml`; содержит API, Web, E2E и PostgreSQL.
Строгое подтверждение S3 API и HTTP: `.github/workflows/minio-dev.yml`; поднимает PostgreSQL и MinIO в Docker, делает два отдельных синтетических набора, выполняет `tests/integration/test_minio_storage.py` и `tests/integration/test_pg_minio_attachment_http.py`, затем делает `docker compose down -v`.

Нельзя считать зелёный bootstrap CI подтверждением FileStorage, если `minio-dev-integration` красный или не запускался.

## Предохранители и оставшиеся риски

- Никаких настоящих личных документов, производства, публичной сети, presigned URL или credentials в браузере.
- OCI mirror MinIO сторонний: pinned digest, но перед production необходима проверка происхождения и собственной сборки образа.
- `PENDING`/QUARANTINED не скачиваются; в обычном dev-обходе сторонний объект из MinIO без записи метаданных никогда не виден API.
- Изменение ACL после начала уже разрешённого HTTP ответа не может отозвать переданные байты; гарантия действует для **следующего** HTTP запроса.
- `purge_old_orphans` — только доверенная maintenance-функция, не HTTP endpoint; она удаляет объекты без метаданных старше часа, не объекты карантина.
- Перед объединением в `main` нужны полные зелёные CI для одного SHA и review; отдельный independent security-review не следует подменять авторскими тестами.
