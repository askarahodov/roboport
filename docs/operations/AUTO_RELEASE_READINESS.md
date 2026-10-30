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


## Интегрированный PG17 сценарий для автомобильного lifecycle

`tests/integration/test_pg_auto_integrated_lifecycle.py` объединяет ранее разрозненные механизмы в одной синтетической транзакционной истории: подтверждённая связь клиента с автомобилем → запись на пост → автоматическое создание WorkOrder → SKU → приход → резерв → выдача → отпуск остатка. Негативные сценарии проверяют чужой tenant для записи и складских операций, а явный rollback исключает фантомное списание. Это расширяет coverage системы, **но не** подтверждает работу денег, согласий, документов, пользовательских экранов или полноценный production E2E — эти блокеры остаются в матрице.


### Продолжение интегрированного сценария: история, отказ и отзыв

PG17 lifecycle теперь также проверяет одновременный tenant-scoped `customer` + складской обзор WorkOrder после RESERVE/ISSUE/RELEASE, отсутствие чужого tenant в обзоре и немедленную недоступность чтения и новых резервов после отзыва WAREHOUSE-роли. Это подтверждает синтетический атомарный read-path и revoke, но не готовность платежей, актов, финансовых остатков, UI и реальной истории клиента к production.


## Контрактная совместимость API в PR CI

В `api` job для Pull Request выполняется `scripts/check_openapi_compatibility.py` по базовому SHA PR из `main` и текущим `contracts/openapi.yaml`, `contracts/auto-dev-openapi.yaml`. Проверка блокирует удаление документированных путей, HTTP-методов, кодов ответов, именованных схем и полей `properties`. Тесты в `tests/contract/test_openapi_compatibility.py` содержат отрицательные случаи. Это **ограниченная** проверка удаления API surface, а не полный semantic/OpenAPI diff: сужение типов, увеличение обязательности входных параметров и изменение поведения требуют независимого ручного review. Исключения на несовместимые изменения не вводятся автоматически; cutover требует отдельного решения #165.


### Дополнительные несовместимости OpenAPI

В дополнение к удалениям путей и ответов PR CI теперь отклоняет новые обязательные параметры операций, удалённые параметры, перевод полей схем в обязательные, изменение верхнеуровневых типов схем и типов их непосредственных свойств. Примеры проверены отрицательными контрактными тестами. Это не полная семантическая проверка: вложенные `$ref`, ограничения форматов, `oneOf`, прав доступа, совместимость поведения и версия данных по-прежнему требуют анализа разработчиком и независимого ревью.


### Path-level параметры OpenAPI

Compatibility gate объединяет параметры, заданные на уровне Path Item и отдельной операции. Операционный параметр с тем же `in`/`name` перекрывает path-level определение (согласно OpenAPI). Новые обязательные, превращённые в обязательные и удалённые эффективные параметры блокируют PR; негативные тесты покрывают оба уровня. Это технический контроль совместимости, а не разрешение production или замена независимого review.


## Синтетический финансовый регистр WorkOrder (#119)

Миграция `0073_auto_order_financial` вводит отдельные неизменяемые записи `CHARGE`/`CREDIT` в minor units и агрегированный баланс по WorkOrder. Функция `record_auto_work_order_financial` обеспечивает request-idempotency, проверку привязки к автомобильной организации и филиалу, сериализацию изменений, запрет кредита сверх баланса и прекращение начислений при отмене записи. `tests/integration/test_pg_auto_work_order_financial.py` проверяет PG17, чужой tenant, повтор, корректировку, rollback и неизменяемость. **Это внутренний синтетический регистр**, не оплата, кассовый чек, СБП, ЭДО, утверждённая смета или бухгалтерская проводка. Публичного API нет; прямой доступ к DB-функции нельзя выдавать production-ролью без отдельного RBAC и независимого security review. #119 остаётся BLOCKED до полного пользовательского и финансового E2E и внешних согласований.


### Интеграция синтетического финансового баланса в PG17 lifecycle

Сквозной интеграционный тест дополнен записью `CHARGE` и `CREDIT` после appointment→WorkOrder→склад. Проверяется идемпотентное повторение начисления, корректировка и итоговый баланс 10000 minor units в том же WorkOrder; уже существующие тесты проверяют чужой tenant, отзыв WAREHOUSE-роли, историю склада и rollback. Финансовые записи пока недоступны клиенту в UI/API и не являются настоящими оплатами или фискальными документами; независимое разрешение и production E2E отсутствуют.


## Закрытый финансовый read-path для автомобильного заказ-наряда

`auto_order_financial_read.get_work_order_balance()` возвращает внутренний синтетический баланс WorkOrder в minor units (`currency: null`, `syntheticOnly: true`) только при активной автомобильной MANAGER-роли нужной организации и филиала и действительной verified, unrevoked связи клиента с автомобилем. Отзыв роли или клиентского proof, чужой tenant, неизвестный order и некорректные параметры блокируют ответ. Реальный PostgreSQL integration test покрывает CHARGE/CREDIT, revoke и tenant isolation. Это **не** публичный API и не полномочие на платежи, налоговый учёт либо продовые данные; до release gate #119 всё остаётся dev-only.


### Синтетический HTTP read финансового баланса

Добавлен GET `/api/v1/dev/auto/organizations/{org_id}/locations/{loc_id}/orders/{order_id}/financial-balance`. Срабатывает только при `ROBOport_SYNTHETIC_AUTO_API=1` и `ROBOport_SYNTHETIC_AUTH_API=1`, прямом HTTPS loopback без прокси и действительной cookie `rp_session`; нужен действующий MANAGER доступ к организации/филиалу и подтверждённая связь автомобиля с клиентом. Ответ `{orderId,balanceMinor,currency:null,syntheticOnly:true}` с `Cache-Control: no-store`. Ошибки: 403 для отсутствия полномочий или чужого объекта, 404 вне dev-контура, 422 для некорректных UUID. Нет оплаты, фискализации, бухгалтерского учёта или production API. Проверки HTTP: `tests/integration/test_pg_auto_order_financial_http.py`.


### Внутренняя финансовая история WorkOrder

GET `/api/v1/dev/auto/organizations/{org_id}/locations/{loc_id}/orders/{order_id}/financial-history?limit=50` (максимум 100) возвращает внутреннюю синтетическую историю начислений и корректировок (`items` с `id`, `action`, `amountMinor`, `reason`, `createdAt`) и `balanceMinor`. Маршрут доступен только через прямой HTTPS loopback с dev-флагами, ролью MANAGER нужного tenant/филиала, действующим клиентским подтверждением; `Cache-Control: no-store`. Неправильный scope, отозванная роль, отозванная связь клиента дают 403, некорректный limit — 422, отключённый dev-контур — 404. Это не платёжная история, чек, полноценный финансовый документ или public/prod endpoint. Сценарий проверяется `tests/integration/test_pg_auto_order_financial_history_http.py`; release #119 остаётся заблокированным.
