# ROBOport — obligatory instructions for all AI coding agents

**Language:** Russian for requirements, task reports, comments in business code; Python/TS identifiers in English. **Read `TECHNICAL_SPEC.md` before editing.** Do not extrapolate requirements from visual prototypes; security is never a frontend check.

## Orchestration workflow
Orchestrator creates a dependency DAG from `docs/implementation/WORK_BREAKDOWN.md`, selects one small vertical slice with measurable acceptance, summons domain specialists (backend/frontend/data/QA/Security/UX) only when useful and supplies exact file paths and contract. Delegation is expected on meaningful parallelizable subtasks; review must be independent of implementation. Do not spawn unlimited redundant agents. Every agent reports observable artifact, test output and blockers.

**Definition of task:** ticket ID, goal, modules, exact input, non-goals, architecture constraints, acceptance tests, test commands, negative test and dependency. Work in feature branches/PRs. No secret/PII in branches, logs, commits or issues. Public repo.

**Standard sequence:** inspect source & ADR → identify contradiction and decide by ADR → implement smallest correct change → run local format/lint/typecheck/tests → review security/contract migrations → peer QA independent check → summarize changed files/test results → PR. If tests fail, repair root cause and rerun. If unavoidable external blocker (PostgreSQL, packages, Keycloak), label `BLOCKED` and **never** claim integration passed.

**Nonnegotiable domain rules:**
- No customer A sees B order/attachment, including previous owner after sale.
- Safety hold blocks servicing; QC_FAIL blocks handover; part selection requires confirmed variant compatibility.
- SENT/APPROVED estimate immutable; new version requires fresh consent.
- Every API mutation checks durable idempotency; replay authorization is rechecked.
- Audit append-only, data redaction and proof of claims; no trust in header customer_id.
- No real external calls, purchases, brand publication or deploys without explicit user decision.

**Agent role charter**:
- Architect: ADR, data boundaries, migration/contract review, no feature creep.
- Backend: use-case+guard+transaction+outbox/API tests, not route-only mocks.
- Frontend: semantic token Light/Dark/Auto, WCAG, shared components, typed generated API, no UI-only security.
- DB specialist: real disposable PG 17 smoke, concurrency, backup/restore, no draft migration auto-apply.
- Security: threat model, 401/403/404 leak prevention, OIDC, S3, idempotency replay.
- Tester: actually execute relevant suites, record green/red and evidence, no optimistic reports.
- Docs: update canonical spec & task traceability alongside code; never replace tested source with fictional status.

**For every PR report (mandatory):** `Scope`, `Contract diff`, `Migrations`, `Tests executed with exit codes`, `Security negative tests`, `Documentation`, `Unverified`, `Next dependency`. STOP with explicit blocker if API ownership or schema conflicts cannot be resolved safely.
