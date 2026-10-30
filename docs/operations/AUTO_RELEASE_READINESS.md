# Автомобильный production release: консолидированная проверка готовности (#119)

**Состояние проверки:** 30.10.2026; **решение: BLOCKED — production release не разрешён**.

Документ — единая карта критериев приёмки и недостающих доказательств, а не подписанное release approval. Статусы основаны на GitHub Issues и подтверждённых синтетических CI; закрытие отдельной Issue или 5/5 PASS не означает выполнение критериев производства.

## Что реально подтверждено

- В CI `.github/workflows/ci.yml`: пять jobs — `postgres`, `api`, `web`, `browser-e2e`, `windows-cmd`; PG17 Alembic upgrade→downgrade→upgrade и синтетический `pg_dump/pg_restore` на disposable стенде, контрактные и браузерные тесты, Docsify навигация и ссылки.
- PR [#298](https://github.com/askarahodov/roboport/pull/298): 5/5 CI PASS; merge `3fffbbb4a47a060b7e66659877f535ff59237a1b`. Ранее [#297](https://github.com/askarahodov/roboport/pull/297) аналогично прошёл 5/5. Тесты проверяют dev-контур, не production tenant enforcement.
- `scripts/check_auto_release_evidence.py` валидирует только структуру восьми категорий JSON и безопасный синтаксис URL/времени. Скрипт **не** проверяет доступность и подлинность evidence, независимость/полномочия подписанта или юридическую допустимость.
- `docs/operations/SECURITY_RELEASE_GATES.md` прямо запрещает реальные клиентские данные, платежи и внешний запуск без отдельного approval.

## Матрица блокирующих доказательств

| Область | Минимум для снятия блокировки | Состояние / источник | Ответственная сторона |
|---|---|---|---|
| Сквозной E2E `запись→WorkOrder→склад→деньги→история` | Реальный end-to-end автомобильный процесс, contract+PG17+browser E2E, tenant/role/revoke | **BLOCKED**: [#113](https://github.com/askarahodov/roboport/issues/113), [#114](https://github.com/askarahodov/roboport/issues/114) OPEN; синтетический read не равен финансовому циклу | Product + engineering + QA |
| Организации/филиалы/ADMIN | Подтверждённый владелец, production bootstrap, полная изоляция customer/order/stock/finance, независимый BOLA/RBAC review | **BLOCKED**: [#166](https://github.com/askarahodov/roboport/issues/166) закрыта в GitHub 30.10, но исходные AC не отмечены выполненными; dev-only реализация ≠ production | Security + operator + engineering |
| Legacy cutover, миграции, восстановление | Полная инвентаризация robot UI/API/docs, dry-run cutover на копии данных, утверждённый rollback и восстановление | **BLOCKED**: [#165](https://github.com/askarahodov/roboport/issues/165), [#172](https://github.com/askarahodov/roboport/issues/172) OPEN; CI synthetic dump/restore не подтверждает production recovery | Data + DevOps + operator |
| Consent, ПДн, retention | Утверждённые правовые основания, информирование и согласия, сроки хранения/удаления, доступ, audit | **BLOCKED**: утверждённые юридические документы не представлены | Legal + privacy/DPO |
| OEM/нормочасы/brand/licensing | Права на источники и условия использования каждого поставщика, коммерческое одобрение | **BLOCKED**: лицензии/подтверждения не представлены | Legal + procurement |
| ККТ, СБП, ЭДО, финансы | Явное решение о scope; если включено — законный fiscal/payment/document flow и тесты | **BLOCKED**: нет подтверждённого production scope и compliance approval | Finance + legal + product |
| Monitoring, SLO, backup, rollback | Runbook инцидента, наблюдаемость, RPO/RTO, упражнение restore/cutover, on-call и rollback approval | **BLOCKED**: синтетическое восстановление CI недостаточно | SRE/DevOps + operator |
| Docs, traceability, independent review | Согласованные матрица функций и продуктовые требования; роли, русскоязычные актуальные шаги, links/secret gate, независимый code/security review | **BLOCKED**: [#121](https://github.com/askarahodov/roboport/issues/121), [#123](https://github.com/askarahodov/roboport/issues/123), [#124](https://github.com/askarahodov/roboport/issues/124) OPEN. `docs/auto/FUNCTIONAL_MATRIX.md` содержит исторические пункты 0021–0032 и требует сверки с main | Tech writer + QA + security |
| CI main и release provenance | Все обязательные checks на **конкретном** релизном SHA, ссылка на CI run + digest артефактов/подпись сборки; независимое review | **PARTIAL**: CI существует и runs проходят; релизный SHA/подписанные артефакты отсутствуют | Release manager |

## Проверка, воспроизводимая на disposable окружении

1. Сверить exact release commit и отсутствие непроверенных изменений в `main`. Работать только с synthetic fixtures без настоящих ПДн и платежей.
2. Проверить статус `.github/workflows/ci.yml` на нужном SHA: все **пять** jobs `success`; отчёт приложить с commit SHA, URL и заключением. Контрактные/PG17/browser тесты не пропускать.
3. Проверить миграции и восстановление по postgres job: Alembic upgrade→downgrade→upgrade и `pg_dump/pg_restore` на disposable базе. Отдельно нужен **production-like** rehearsal на авторизованной обезличенной копии, проверка целостности, согласованные RPO/RTO и rollback.
4. Пройти все пользовательские и operator сценарии, включая отказ доступа чужого tenant, отзыв роли, негативные BOLA, финансовые и документные операции.
5. Предъявить для **каждой** из восьми категорий оригинальную, проверяемую запись с owner, исходным артефактом, датой/версией и независимым подписантом; сверить с `docs/operations/SECURITY_RELEASE_GATES.md`.
6. Структурная предварительная проверка (НЕ approval): `python scripts/check_auto_release_evidence.py path/to/evidence.json`. Не создавать фиктивный полный JSON ради зелёного exit code.

## Правило закрытия #119

Issue #119 и production release можно рассматривать к закрытию **только когда все строки матрицы имеют фактическое evidence**, выполнен production-like rollback/recovery rehearsal, готова документация, доказано отсутствие legacy robot surface, все обязательные CI jobs зелёные на итоговом SHA, а Security, Legal/Privacy, operator и Release Manager независимо подтвердили полномочия и разрешение. Подписи/разрешения не должны самогенерироваться из тестов, README или валидатора.

**Текущий итог: BLOCKED.** Ни этот документ, ни CI, ни closed #166 не являются самостоятельным разрешением хранить реальные ПДн, проводить реальные оплаты или запускать продукт в production.


## Автоматическая проверка происхождения CI для релизного SHA

`python scripts/check_auto_release_ci.py --sha <40-hex-main-commit> --run-id <github-actions-run-id>` проверяет через GitHub API, что указанный прогон относится ровно к нужному SHA, событию `push` основной ветки `main`, стандартному `.github/workflows/ci.yml`, завершился `success`, а все пять jobs (`postgres`, `api`, `web`, `browser-e2e`, `windows-cmd`) присутствуют и завершились `success`. Неполная страница job API (в том числе больше 100 jobs), пропущенные/отменённые проверки, ошибки сети или неверный SHA блокируют результат. Для ограничений GitHub API можно задать `GITHUB_TOKEN` из секретов окружения; значение не логируется.

Это только независимая от self-reported JSON проверка *технического* CI run; она не является юридическим/безопасностным одобрением, не подтверждает подпись артефактов и не снимает остальные BLOCKED строки матрицы. `tests/contract/test_auto_release_ci.py` выполняет негативные проверки без сетевых вызовов.


### Сверка реального формата GitHub Actions

Проверка `check_auto_release_ci.py` принимает ровно `path=.github/workflows/ci.yml`, как возвращает GitHub Actions API; дополнительно сверяет `repository.full_name=askarahodov/roboport` и `id` прогона из CLI. Случайная или умышленная подмена репозитория/прогона блокируется. Это не заменяет независимое approval и не доказывает production readiness.


### Защита от смешивания CI jobs разных запусков

Проверка происхождения CI требует для **каждого** из пяти jobs точного `head_sha`, `run_id` и `run_attempt`, совпадающих с выбранным успешным main run. Отсутствующие значения, чужой commit, иной повторный запуск или подмена части jobs отклоняются. Такая проверка закрывает технический риск ложного 5/5 при смешивании результатов; она всё ещё не заменяет независимое release approval.


## Консолидированный технический preflight (без production approval)

Команда `python scripts/check_auto_release_preflight.py --sha <release-main-sha> --run-id <actions-run-id> --evidence <local-json>` проверяет структуру восьми evidence gates, точное соответствие CI SHA и всех пяти jobs, а также состояние открытых продуктовых/документационных зависимостей #113, #114, #165, #172, #121, #123, #124 через GitHub API. При сетевой ошибке, недоступном JSON или неполном ответе результат — BLOCKED. Вывод содержит только коды причин, без секретов или содержимого доказательств.

Даже при `technicalChecksPassed: true` вывод всегда содержит `verdict: BLOCKED`, `productionApproved: false` и код завершения 1: настоящие юридические, privacy/security и эксплуатационные полномочия и подписи нельзя доказать по данным GitHub и JSON. Этот инструмент предназначен для выявления блокеров; он **не** является командой деплоя или релизным разрешением. Не использовать его как основание для обработки настоящих данных.


## Автоматическая регрессия границы production/dev (#119)

`tests/bootstrap/test_auto_production_boundary.py` удостоверяет, что `/health/ready` возвращает `503 not_ready` независимо от включения синтетических флагов, автомобильные dev-маршруты не входят в публичную схему OpenAPI и внешний HTTP-origin не получает доступ к `/api/v1/dev/auto/*` даже при включённом dev-пилоте. Этот тест запускается в штатном `api` job (`tests/bootstrap`) при каждом PR и main push. Он не является самостоятельным доказательством отсутствия других production API surface и не заменяет независимый security review; снятие 503 возможно только отдельным проверенным релизным изменением после согласования gate.


## Расширенная проверка восстановления auto tenant (#119)

В `postgres` job синтетический `pg_dump/pg_restore` теперь включает запись `auto_organization` и сравнивает её наличие в исходной и восстановленной PG17 базе. После восстановления CI проверяет, что защита неизменяемости кода организации и уникальность `auto_organization.code` продолжают отклонять запрещённые изменения. Вместе с существующим legacy `customer`/`service_ticket` тестом это подтверждает базовый roundtrip для двух доменов; **не** подтверждает миграцию всех реальных автомобильных документов, stock/finance, RPO/RTO, шифрование backup или авторизованный production cutover. Эти проверки остаются блокерами #119/#165/#172.
