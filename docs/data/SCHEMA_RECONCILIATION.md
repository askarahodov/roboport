# Сверка модели данных — актуализация по #17 (16.10.2026)

**Канонический источник решений:** `docs/architecture/ADR.md` (ADR-032); машинная таблица соответствий: `docs/data/SCHEMA_FIELD_MATRIX.json`. Эта страница заменяет устаревшие утверждения о том, что базовая SQL CHECK, FSM, сметы, сессии и ACL не реализованы.

В main проверяются на одноразовой PostgreSQL 17 реальные Alembic миграции `0001_core`–`0014_estimate_decision`; на ветке #21 отдельная, ещё не принятая ревизия 0015. Исторические `db/legacy_drafts/001–006` пустые и запрещены к исполнению.

## Решения по коллизиям

- Каноническая `Ticket.state`: 20 SQL/FSM состояний; допустимый `Transition.targetState` равен набору без DRAFT. В контракте 0.15.0-draft удалены недействительные старые имена и обновлены генерируемые TypeScript-типы.
- Право на историю заявки фиксируется по `service_ticket.customer_id` и живой связи учётной записи с клиентом, а не по текущему владельцу устройства.
- `Estimate.total` форматируется из `estimate.total_minor` как десятичная строка; `SUPERSEDED` вычисляется как устаревшая версия и не добавляется в SQL CHECK.
- `Ticket.reference`, `CreateTicket.symptoms`, `Ticket.modelVariantId`, `Transition.reason` остаются запланированными полями без доказанного production mapping. Решение есть в ADR-032, запрещено выдавать их за реализованные.
- Модель `part_compatibility` с авторизованным доказательством и двойным подтверждением является задачей #18. Синтетический флаг verified из #21 не подтверждает OEM compatibility.
- Receipt-таблицы для FSM и смет уже присутствуют, но единый межоперационный ledger и outbox требуют отдельной интеграции, без ложного заявления о готовности.

## Реальные проверки

`python scripts/check_contract.py` и `PYTHONPATH=apps/api python -m pytest -q tests/bootstrap tests/contract` сверяют OpenAPI, SQL-колонки по исходникам миграций, FSM, ADR и legacy-файлы. Для подтверждения бизнес-инвариантов отдельно требуется GitHub Actions PostgreSQL integration; статические тесты не заменяют реальные транзакции.

## Ограничения

Матрица различает `implemented` и `planned`. Статус implemented подтверждает наличие SQL-поля, а **не полную production готовность соответствующего handler**. Реальные customer данные запрещены до отдельных release-gates; никаких исторических SQL не запускать.

---
## Исторический аудит

# Аудит источников модели данных — checkpoint #17

**Дата:** 2026-10-15. **Статус:** частичный аудит; миграции #11 заблокированы до определения полной схемы.

## Установленные факты

- `db/legacy_drafts/001_initial.sql` … `006_idempotency_ledger.sql` существуют, но все шесть имеют **0 байт**. Их нельзя считать DDL, переносить в Alembic или использовать как доказательство полей / FK.
- `docs/domain/TICKET_FSM.md` задаёт 20 проектных состояний, но `components.schemas.Ticket.properties.state` теперь содержит 20 enum-значений из `docs/domain/TICKET_FSM.md` (OpenAPI `0.14.0-draft`); SQL CHECK и проверка разрешённых переходов пока отсутствуют.
- `docs/data/DATA_MODEL.md` перечисляет таблицы и инварианты, а не миграционную схему.

## Разграничение ответственности

| Поле / связь | Планируемое хранение (НЕ утверждённый DDL) | API | Правило доступа и тест |
|---|---|---|---|
| service_ticket.id | UUID PK | Ticket.id | проверка ownership / ACL |
| service_ticket.customer_id | FK → customer.id, NOT NULL | CreateTicket.customerId | заявитель фиксируется на момент обращения |
| service_ticket.state | constrained enum после согласования FSM | Ticket.state | только guarded transition, не произвольный PATCH |
| service_ticket.version | integer > 0, optimistic lock | Ticket.version | одновременный переход → 409 |
| service_ticket.device_id | FK → robot_device.id, nullable при запросе | CreateTicket.deviceId | владение роботом не раскрывает чужие прошлые заявки |
| estimate.version | integer, immutable after SENT | согласование сметы в контракте | нельзя одобрить старую редакцию |
| user_session.token_hash | binary/string hash; только сервер | localSession cookie, opaque | отзыв и CSRF, без исходного token в БД |
| idempotency_request.key_hash | hash + caller + fingerprint | Idempotency-Key | повтор требует повторной авторизации |

## Решения и открытые расхождения

1. **Правило владельца:** ticket принадлежит заявителю `service_ticket.customer_id`; смена `device_ownership` не переносит доступ к старому ticket. Это уточнение ADR-031.
2. **Критический GAP:** пустые SQL 001–006. До #11 нужно написать новую нормализованную baseline-схему из актуальной модели, а не запускать пустые legacy файлы.
3. **Частично закрыто:** OAS `Ticket.state` приведён к 20 состояниям FSM; остаётся согласовать охраняемые переходы, SQL CHECK и интеграционные тесты PostgreSQL.
4. **Открыто:** точные CHECK, FK, nullable, последовательности смет, SKU compatibility, audit/outbox/idempotency locking. Не отмечать #17 выполненной до подтверждения каждой связи.

**Запрет:** не использовать живые данные клиентов, не применять файлы `db/legacy_drafts` к PostgreSQL и не закрывать #11/#17 из-за зелёных статических тестов.

## PostgreSQL 17 checkpoint — ограниченная baseline 0001_core

**Проверено на disposable PostgreSQL 17 в GitHub Actions, а не на реальной БД.**
- `apps/api/alembic/versions/0001_core.py`: новая обратимая миграция customer, robot_device, service_ticket, ticket_state_event, audit_event. Не является полной схемой проекта и не использует пустые legacy SQL.
- `alembic upgrade head → downgrade base → upgrade head` проходит в CI.
- Проверены настоящими SQL-запросами: отказ по отсутствующему customer FK, отказ неизвестному Ticket.state, отказ version=0; trigger запрещает менять заявителя существующей заявки; второй trigger запрещает UPDATE/DELETE audit_event.
- БД создаётся как service container `postgres:17` только на GitHub runner, строка подключения задаётся обязательным `ROBOport_DATABASE_URL`.

**Не реализовано и не заявлено:** ограничения concurrent owner, frozen SENT estimate, stock, idempotency ledger, downgrade с реальными данными, `pg_dump/pg_restore`, полноценный runtime FSM/ACL и независимый DBA review. #17/#11 остаются открытыми. Нельзя развёртывать baseline как production schema.

## 0005_quote_race — упорядочивание версий сметы и решений

Ревизия `0005_quote_race` обязывает INSERT новой версии сметы брать `FOR UPDATE` на `service_ticket`, а INSERT решения по смете брать тот же ticket lock **до** блокировки сметы. Это устраняет прежний несовместимый порядок lock estimate → ticket и сериализует операции создания версии и одобрения на одном ticket. Новая версия после `APPROVED/DECLINED` не допускается без отдельного workflow/ADR. Если новая версия появилась первой, старое согласование отклоняется как stale.

Тесты на реальной PostgreSQL проверяют запрет новой версии после решения и отклонение устаревшей SENT сметы при наличии более новой DRAFT. Эти тесты **не являются стресс-тестом двух одновременных транзакций разных типов**; независимые права, согласие клиента и idempotency API по-прежнему не реализованы.
