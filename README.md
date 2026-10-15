# ROBOport — AI-first development monorepo

**Status:** development, synthetic fixtures only; no customers, public service, purchases or vendor outreach. **Working name**: trademark/brand review is not a prerequisite to writing code but blocks external release.

**Canonical entrypoint:** [`TECHNICAL_SPEC.md`](TECHNICAL_SPEC.md). Read [`AGENTS.md`](AGENTS.md) and [`docs/implementation/WORK_BREAKDOWN.md`](docs/implementation/WORK_BREAKDOWN.md) before changing files.

## Product
Multibrand household robotics repair/service platform: public site and limited catalog; required customer account; staff CRM for intake, diagnosis, estimates, verified part compatibility, inventory, QC, handover, documents and audit. Launch manufacturers are **research candidates** Dreame, Roborock, Xiaomi, not authorized repair relationships. Humanoids/quadrupeds are **out of service scope**.

## Stack baseline (ADR-021)
Python 3.13 + FastAPI + Pydantic v2; PostgreSQL 17.x (supported, dev target) + SQLAlchemy 2.0 + Alembic; Keycloak-compatible OIDC; S3-compatible private object store; React 19 + TypeScript + Vite 8; Node.js 24 LTS; Docker Compose for local dependencies; GitHub Actions for CI. No Kubernetes requirement for local MVP. Version tags are **targets**, not evidence packages are installed; use lockfiles before deploy.

## What runs today
`apps/api` is a *new minimal application bootstrap*, not a service-business implementation. Tests verify health/meta, restricted operations and placeholders; **no real identity, DB, client account or stock workflow exists**.

```bash
python -m pip install -r apps/api/requirements-dev.txt
PYTHONPATH=apps/api pytest -q tests/bootstrap
PYTHONPATH=apps/api uvicorn roboport_api.main:app --host 127.0.0.1 --port 8000
```

Frontend is a React/Vite source scaffold, dependencies need `npm install`/lockfile before its first build. The design prototype (no backend) is available as `design/prototype/index.html`, open locally to inspect day/night/auto and 401/403/409/Safety/QC screens.

## Project layout
- `TECHNICAL_SPEC.md` — one authoritative end-to-end development assignment (Russian).
- `docs/` — bounded contexts, ADR, RBAC/FSM, API lifecycle, data model, threat model, tests, operations, design and agent workplan.
- `contracts/openapi.yaml` — existing **draft** OpenAPI 3.1.2, 42 operations; currently 14 served only in archived synthetic test adapter, none certified production-ready.
- `db/legacy_drafts/` — six **untested** historical SQL drafts; **DO NOT** auto-apply; new Alembic baseline required.
- `design/` — original C Industrial Lab angular R and A Precision palette, day/night/auto, editable tokens and previews.
- `apps/api`, `apps/web` — initial code scaffolds with explicit nonimplemented behaviors.
- `tests/` — bootstrap/contract checks, no live PostgreSQL test yet.
- `.github/workflows/ci.yml` — bootstrap CI workflow requiring third-party packages.

## Important gates
Development may begin immediately using test identities and synthetic content. Production/real-data use must not occur before PostgreSQL migration tests, real OIDC/ABAC, documented S3 isolation, independent security/legal review, approved trademark/public design and economic go/no-go. Internal implementation is **not** approval for deployment.

Last updated 2026-10-15.
