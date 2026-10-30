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
