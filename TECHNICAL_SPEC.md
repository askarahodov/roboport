# Техническое задание на разработку ROBOport — каноническая спецификация v1.0

**Дата:** 15.10.2026 · **Статус:** baseline for implementation, не разрешение эксплуатации · **Репозиторий:** `askarahodov/roboport` (публичный: никаких настоящих данных, адресов, телефонов, сертификатов, ключей, внутренних URL). **Законодательные/коммерческие вопросы:** ведутся отдельным track и не блокируют кодирование на синтетике; обязательны до реального клиента, платежа, печати и публикации.

## 0. Приоритет и источник истины
1. Данный `TECHNICAL_SPEC.md` — главный приказ для AI-агентов. Изменения требований только через PR+ADR+обновление тестов. При расхождении с Google Docs, заметками или ранним HTML приоритет: утверждённый ADR → эта спецификация → `docs/*` → `contracts/openapi.yaml` как **draft**, который приводят в соответствие решениям → архивы `db/legacy_drafts` как неисполняемые источники гипотез.
2. AGENTS.md обязателен для всех ролей: orchestrator, architect, backend, frontend, QA, DevSecOps, docs; агент обязан действительно выполнять проверки, не выдавать мок за интеграцию и вести доказательства в PR.
3. Два разных вида готовности: **dev-ready** — можно писать код и тестировать синтетикой; **release-ready** — есть все проверки безопасности, юридические и сервисные разрешения. Запуск реального сервиса запрещён до release-ready.

## 1. Продукт и границы MVP
Система для независимого мультибрендового сервиса бытовой робототехники. Обязательные контуры: `PUBLIC WEB`, `CUSTOMER ACCOUNT`, `STAFF CRM`, `CATALOG & COMPATIBILITY`, `STOCK`, `DOCUMENTS`, `AUDIT`, `NOTIFICATIONS` и admin-настройки. Продажи роботов, официальный OEM статус, B2B multi-tenancy, управление прошивками и двигателями гуманоидов, маркетплейс, оплата, сложная AI-диагностика **не входят** в MVP.

Пилотные бренды **как исследовательские кандидаты**: Dreame, Roborock, Xiaomi. Ни одна модель не получает `SUPPORTED` до проверки точной SKU, региона, ревизии, станции и квалификации исполнителя. Unitree/AGIBOT, робособаки и гуманоиды: `RESEARCH_ONLY` + `SERVICE_FORBIDDEN`, пока отдельная инженерная safety-программа не завершена.

Стороны: посетитель; клиент; сотрудник приёмки; инженер; кладовщик; менеджер/контролёр; финансы; администратор; аудитор. Физическое право на устройство и право на конкретный заказ — **разные сущности**; при передаче робота новому владельцу старые документы не открываются.

## 2. Функциональные модули и use-cases
### PUBLIC WEB
- PW-01: главная, как работает сервис, категории и услуги, FAQ, контактные страницы (в dev только placeholder).
- PW-02: каталог брендов/моделей/вариантов, публичное состояние `CANDIDATE | VERIFYING | SUPPORTED | RESTRICTED | UNSUPPORTED`; **не подменять** `CANDIDATE` на `SUPPORTED`.
- PW-03: профиль модели не включает скрытые инженерам данные и запчасти с неподтверждённой совместимостью.
- PW-04: создание гостевого обращения — feature flag OFF пока нет утверждённых оснований обработки персональных данных; в dev исключительно синтетика.
- PW-05: ограниченный статус по токену как вспомогательный маршрут (не заменить им кабинет); токен передаётся **в HTTP header**, не в URL, не логируется, хранится как hash и истекает.

### CUSTOMER ACCOUNT (обязательный MVP)
- CA-01: OIDC вход, выход, восстановление сессии, step-up для чувствительных действий, доступность без паролей внутри ROBOport; `account_identity` отделён от `customer`.
- CA-02: `Мои роботы`: устройства, точный variant, маскировка серийника, безопасная история владения; новый владелец не видит чужие ремонты.
- CA-03: `Мои заявки`: статус, таймлайн, действия, диагностика в рамках disclosure policy.
- CA-04: смета: только своё обращение, точная **SENT** версия, сумма и строки immutable после отправки, ответ approve/decline с серверно созданным доказательством; повторное согласование той же версии не даёт повторного эффекта.
- CA-05: доступ к квитанциям/актам/гарантии через отдельно авторизованную ссылку TTL, no-store и аудит; приватный S3, никакого публичного ACL.
- CA-06: персональные предпочтения уведомлений (операционные и маркетинговые цели разделить).
- CA-07: защищённое связывание гостевой заявки со своим аккаунтом: reference/телефон/SN недостаточны; отдельный proof + staff review; до подтверждения объект скрыт.
- CA-08: повторное обращение, профили, изменение контакта только с reverify; отклонённая/отозванная связь запрещает дальнейший доступ, включая idempotent replay.
- CA-09: Light/Dark/Auto; логотип C Industrial Lab угловая R и палитра A Precision, одинаковая доступность на мобильном.

### STAFF CRM
- CRM-01: очередь/поиск/фильтры, назначение исполнителя, клиентские PII только по роли и заявке; не делать общий unrestricted GET.
- CRM-02: приёмка, комплектация устройства/станции, фотографии по основанию, журнал и подписанное подтверждение.
- CRM-03: диагностика, safety flag, `SAFETY_HOLD` с запретом операций и двойным review для снятия.
- CRM-04: сметы с immutable версиями, отправка, согласие, отказ, отмена и supersede с аудитом.
- CRM-05: склад, поставщики, входящие партии, точные P/N и region SKU/ревизии; check verified compatibility; транзакционная бронь без отрицательного остатка.
- CRM-06: worklog работ, QC PASS/FAIL, повторная проверка; выдача устройства только после QC PASS, разрешённого состояния и получателя по регламенту.
- CRM-07: отчёты по задержкам/возвратам/причинам отказа, аудит и права; сервисные сообщения из transactional outbox.
- CRM-08: справочники моделей, поддерживаемых операций, сотрудников и прав; допуск операции должен проверять два разных ответственных участника.

### DOCUMENTS & NOTIFICATIONS
- DOC-01: метаданные в PG, blob в private S3; MIME allowlist, quota, malware/quarantine workflow (dev может быть stub), подписи URL только после object-level auth.
- NTF-01: transactional outbox в одной транзакции со сменой заказа; worker с retries/backoff/dead-letter, без внешних доставок в dev.

## 3. Чёткие нефункциональные требования
- Безопасность: OIDC issuer/audience/JWKS verification, expire/revoke, least-privilege, ABAC/BOLA on **every** object; deny-by-default, CSP/CSRF where applicable, no direct trusting `customerId` header. Изменения ролей и доступа аудируются.
- PII: в dev только fixture; журналы не содержат токены, содержимое документов, телефоны, email, полные серийные номера; encrypted transport TLS на стендах, secrets via environment/secret manager, no keys in Git.
- Database: PostgreSQL ACID, UTC timestamptz, migration via Alembic, FK/unique/check/partial indexes, transactions and row locks; idempotency durable account+key+fingerprint.
- Contract: OpenAPI 3.1.2, единый Error `{code,message,requestId}`, `X-Request-ID` server-issued, 401/403/404/409/422/429/500 documented and tested; errors do not disclose existence of another customer's ticket. 42 drafted operations as target, **not a statement of implementation**.
- UX: responsive 390px and desktop; screenreader, keyboard, visible focus, contrast WCAG 2.2 AA target, semantic error/safety states both Light/Dark/Auto.
- Observability: structured JSON logs, correlation ID; metrics HTTP, latency, outbox lag, stock conflicts, denied auth, diagnosis safety; PII redaction; health `/health/live`, `/health/ready` (ready fails if dependencies unavailable).
- Reliability targets *to validate during load tests*: backend availability ≥99.5% per month after GA, p95 API reads ≤500ms for representative sample, mutations ≤1000ms excluding uploads, RPO ≤24h / RTO ≤8h for initial pilot; targets NOT yet verified or contractual.
- Locale: RU first, strings externalized; EN future; dates UTC at rest and localized in UI, money in integer minor units / explicit RUB currency, no floats for finance.

## 4. Технический стек — принято для первой реализации
| Подсистема | Технология | Причина / контроль |
|---|---|---|
| Backend | Python 3.13, FastAPI, Pydantic 2 | Existing OpenAPI/TestClient PoC; typing and contract tests |
| Persistence | PostgreSQL 17.x, SQLAlchemy 2.0.x, Alembic, psycopg3 | ACID and row-level constraints; migrations need real PG tests |
| Frontend | React 19, TypeScript 5.x, Vite 8, React Router, TanStack Query | SPA for site + customer + staff, shared design system; SSR deferred; public SEO review separately |
| Identity | Keycloak-compatible OIDC (realm for dev test), PKCE | external IdP; staff/client roles; MFA on sensitive actions |
| Files | S3-compatible private store (MinIO locally) | encrypted and signed read links; access checks in API |
| Async | DB outbox + Python worker; no Kafka in MVP | atomic events and retry without premature microservices |
| CI | GitHub Actions; Python pytest/ruff/mypy; ESLint/TS/Vite/Playwright; dependency scans | reproducible gates; lockfiles required |
| Runtime | Docker Compose for dev only; container images for later staging | don't require Rancher/RKE2/K8s for MVP dev |
| Design | SVG angular R C + A teal tokens Light/Dark/Auto | chosen owner direction; assets kept editable |

**ADR:** modular monolith, no NestJS/Next.js swap without new ADR; production infra/cost decisions after baseline tests. Versions must be locked by dependency files/CI; package ranges in scaffolds aren't lockfiles. See `docs/architecture/ADR.md`.

## 5. Архитектура и границы данных
`apps/web` (routes `/`, `/robots`, `/account/*`, `/staff/*`) → `apps/api` REST `/api/v1/*` → domains `catalog, identity, customer, ticket, estimate, compatibility, stock, quality, documents, notification, audit`. All domains share one PG database with explicit module ownership; changes through services, no bypass via UI. Blob store and Keycloak external. Notifications via outbox worker. See `docs/architecture/BOUNDARIES.md`, `docs/data/DATA_MODEL.md`.

### Источники истины и связь
- `customer_account/account_identity` — verified login; `account_customer_link` — explicit proof and revocation; `customer` — legal customer; `device_ownership` — owner over time; `service_ticket.customer_id` — applicant/rights for that ticket; `attachment.ticket_id` — document protection.
- `robot_model/model_variant/dock_variant` — model identity; `service_capability` exact variant & operation, approved by authorized parties; `part_compatibility` exact P/N and variant revision.
- `estimate/estimate_line/estimate_approval` — immutable financial version; `inventory_reservation` and `stock_movement` require atomicity; `ticket_state_event` and `audit_event` append-only; `notification_outbox` independently retryable.

## 6. FSM заказа и непреодолимые ограничения
Canonical graph is `docs/domain/TICKET_FSM.md`. Include full statuses `DRAFT,SUBMITTED,TRIAGE,AWAITING_DELIVERY,RECEIVED,DIAGNOSING,SAFETY_HOLD,ESTIMATE_DRAFT,AWAITING_APPROVAL,APPROVED,DECLINED,AWAITING_PARTS,REPAIRING,QC_PENDING,QC_FAILED,READY_FOR_HANDOVER,COMPLETED,CANCELLED,UNREPAIRABLE,WARRANTY_ROUTE` (exact names/types must be reconciled with existing SQL and contract before migration). Generic `status=...` PATCH prohibited; use transition endpoint with explicit event, actor, current version and guards. Safety hold freezes work; no handover after QC FAIL. Report any incompatible existing names as ADR and tests, not silent renames.

## 7. REST contract and error model
`contracts/openapi.yaml` is the v0.10 draft baseline (42 operations, 56 schemas), preserve operationIds where possible. New backend implements by vertical slice; **do not assert 42 implemented**. All mutation operations need authorization + Idempotency-Key with proper semantics (replay returns same response; altered fingerprint 409; authorization rechecked on replay). Cursor pagination, filtering, stable envelope and Problem/Error normalization tested against OpenAPI for 2xx and 4xx. Work under `docs/api/API_IMPLEMENTATION.md`. Do not use public token in URL; don't expose complete diagnosis in emails/SMS.

## 8. RBAC/ABAC
Roles: `PUBLIC, CLIENT, INTAKE, ENGINEER, STOCK, MANAGER, FINANCE, ADMIN, AUDITOR`; no implicit ADMIN universal right over PII. The API verifies role and object relationship, scope, ticket state, service capability and data class. Table in `docs/security/SECURITY_MODEL.md`. Required negative tests: A cannot access B's ticket/attachment, device transfer B cannot read A's history, duplicate approve, stale estimate, revoked account, forged headers, wrong stock/part, safety hold bypass, QC FAIL handover, replay after logout.

## 9. UX and design-system handoff
**Logo**: owner-selected C Industrial Lab original angular R, never hexagonal A. **Palette**: A Precision Service muted teal. **Themes**: Light/Dark/Auto, persistent preference (local pref allowed for appearance only). Shared CSS tokens, components, states `loading,empty,401,403,404,409,SAFETY_HOLD,revoked`, basic keyboard behavior, responsive breakpoints; CRM uses denser tables and still accessible. `design/prototype/index.html` remains self-contained synthetic demo for visual reference. Brandbook preliminary signed-off **as working design baseline only**; public commercial use blocked by trademark/font/OEM review.

## 10. Implementation slices (strict dependency order)
1. **S0 Repository/CI**: structure, AGENTS, ADR, health endpoint, pytest, OpenAPI static validation, frontend dev build, no env secrets. `dev-ready` after green checks.
2. **S1 DB migration**: new Alembic initial schema, stand-alone disposable PostgreSQL 17, negative FK/constraint/trigger/reservation tests, backup/restore. Draft SQL 001–006 are read-only research until all conflicts reconciled.
3. **S2 Identity/authorization**: dev Keycloak realm, strict OIDC issuer/audience/PKCE/JWKS, CLIENT/ST AFF roles, ownership proof, deny-by-default. Synthetic TestClient and actual IdP tests.
4. **S3 Catalog & compatibility**: exact variant, service capabilities, part P/N approval, public filters; no candidate auto-support.
5. **S4 Ticket + intake + FSM + audit**: create, intake, diagnose, Safety hold; current version and guarded transitions.
6. **S5 Estimate + approval + stock**: immutable estimate version, ownership, double approval, stock reservation concurrency and idempotency/outbox.
7. **S6 Customer Portal**: authenticated 6 sections, documents secure path, notification settings, guest claim step-up and replay tests. 
8. **S7 Staff CRM + QC/handover**: seven sections, accurate roles, QC FAIL blocks, accessible Light/Dark.
9. **S8 Public site/forms**: release-safe catalog, legal toggle controls, status header token and SEO/accessibility.
10. **S9 Pilot readiness**: disaster recovery, threat modelling, WCAG audit, no real users without legal/IP/financial gates.

Slice acceptance and dependencies are enumerated as tickets in `docs/implementation/WORK_BREAKDOWN.md` and `docs/quality/ACCEPTANCE_TESTS.md`. If a lower slice fails, fix root cause before proceeding.

## 11. Delivery / Definition of Done (DoD) mandatory for each PR
- Requirement IDs and ADR linked; no speculative major dependencies.
- Source + tests + migration (if applicable) + OpenAPI contract change + docs + release note; public data synthetic.
- Execute relevant unit + integration + typecheck + lint + security checks and report actual command/exit status (or BLOCKED with real cause, never green claim).
- Reviewer/TESTER agent independent; negative auth/stock/safety tests; manual specialist checkpoint before merges touching safety/payment/customer PII.
- Regression suite green, no high severity known vulnerability, reproducible local steps, screenshots for UI Light/Dark/390px. Never skip quality gates to satisfy timeline.

## 12. Agent orchestration protocol
Use `AGENTS.md`: Planner/Architect splits dependency DAG into small tasks; Builder writes bounded module, QA executes tests independently, Security evaluates threat model and fails closed, Docs updates contract. Each task must include inputs, outputs, files, dependencies, tests, failure recovery and explicit stop criterion. No agents deploy publicly, spend, contact vendors or process real data without explicit authorization. Proceed autonomously while green; red -> root cause -> retest; unexplained integration barrier -> report as blocker and work on unrelated tasks only.

## 13. Delivery and environment
Development `localhost` only, no cloud resource creation required. `compose.dev.yml` is **future dev compose** until tested with Docker and disposable PostgreSQL; do not ship or run against any real database. `.env.example` placeholders only; `SECURITY.md` describes key isolation. CI never references real IDs. Production infrastructure only after separate ADR and review. Document backup restoration and migrations before any deployed DB.

## 14. Phased release gates and exclusions
`G-DEV`: source/tests/CI; `G-DB`: migrations & backups actual PG; `G-AUTH`: genuine OIDC and ABAC; `G-DOC`: secured S3; `G-DESIGN`: approved brand & usability; `G-SECURITY`: independent audit; `G-LEGAL`: lawful data processing+trademark; `G-BIZ`: economic go/no-go. **Only G-DEV authorizes development; none of these are automatically evidence of public launch.**

## 15. Traceability / inherited artifacts
Canonical source notes archived in Google Drive `robo` (not mirrored to public GitHub due sensitive/non-public context). OpenAPI draft and SQL experiments carried forward from TechPrep v0.10; latest design reference v0.4. Existing legacy 115 FastAPI tests validate synthetic adapter, **not production backend**. Design v0.4 has 91 structural/browser tests, **not user-facing readiness**. See `docs/implementation/SOURCE_INVENTORY.md` and baseline check in `docs/quality/ACCEPTANCE_TESTS.md`.
