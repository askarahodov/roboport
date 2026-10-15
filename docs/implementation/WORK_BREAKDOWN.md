# Implementation backlog — strict dependency DAG for AI coding agents

**Do not reorder silently.** Historical Google Sheet backlog includes 111+ planning tasks; GitHub issues below are implementation work units, not a claim prior Google statuses are complete. Main PR branch name `feature/RP-NNN-topic`, focused diff, acceptance evidence required.

| ID | P | Depends | Scope | Deliverable / acceptance |
|---|---|---|---|---|
| RP-001 | P0 | — | repository foundation | README, SPEC, AGENTS, ADR, public-safe policy, CI and smoke green |
| RP-002 | P0 | RP-001 | backend scaffolding | FastAPI package, configuration, live/ready, error model, pytest, structured log; no real data |
| RP-003 | P0 | RP-001 | frontend scaffold | Vite/React/TS router + tokens Light/Dark/Auto + 390px, build & Playwright after install |
| RP-004 | P0 | RP-001 | contract canonicalization | OAS lint/SDK generation, route/errors/idempotency policy, 2xx+4xx tested |
| RP-005 | P0 | RP-002,RP-004 | Alembic baseline | compare 6 legacy SQL drafts; fresh PG 17 migration + real smoke/backup |
| RP-006 | P0 | RP-002,RP-005 | identity | dev Keycloak/OIDC, JWT validation, client/staff scopes and two-user negative tests |
| RP-007 | P0 | RP-005,RP-006 | catalog/capability | variants, verified service ops, two approvals; not supported by default |
| RP-008 | P0 | RP-005,RP-006 | tickets/intake FSM | guarded transitions, custody, diagnosis safety and audit |
| RP-009 | P0 | RP-008,RP-006 | estimates | immutable SENT, exact version, proof and reject/supersede |
| RP-010 | P0 | RP-005,RP-007,RP-009 | stock | exact compatible P/N, concurrency, no negatives, movements |
| RP-011 | P0 | RP-005,RP-009 | durable idempotency/outbox | two-process race + crash/retry and atomicity |
| RP-012 | P0 | RP-006,RP-008,RP-009 | customer account | own devices/tickets/estimates, guest claim, revoke |
| RP-013 | P0 | RP-005,RP-006,RP-012 | secure documents | private S3, quarantine, short TTL signed URL, audit |
| RP-014 | P0 | RP-008,RP-010 | staff CRM | diagnosis, estimate, stock, QC FAIL and handover RBAC |
| RP-015 | P1 | RP-003,RP-012,RP-013 | customer frontend | all six screens, errors, accessibility, responsive, contract SDK |
| RP-016 | P1 | RP-003,RP-014 | staff frontend | seven workflows, safety hold, QC fail, object guards |
| RP-017 | P1 | RP-003,RP-007 | public frontend | SEO/legal safe, catalog, request feature flag OFF until legal |
| RP-018 | P0 | RP-001 | brandbook working package | approved **development** C logo A colors day/night, SVG/fonts rules, touchpoints; legal public gate open |
| RP-019 | P0 | RP-003,RP-018 | design system & UX QA | tokens, components, keyboard, dark/light, error/safety, diff snapshots |
| RP-020 | P0 | RP-005,RP-011,RP-012,RP-014,RP-019 | integration & readiness | disposable PG+IdP+S3, full security matrix, backup/recovery, independent QA |

**Milestones:** M0 RP001–004+018 design-dev baseline; M1 RP005–008; M2 RP009–013; M3 RP014–019; M4 RP020 then external legal/commercial gate. Each issue starts `Not started` until actual work, not from prior design test reports. Lead agents use `AGENTS.md` and split tasks into PR-sized units. No merges into main while blocking tests red.
