# Development environment and CI

Baseline: Python 3.13, Node.js 24.x, PostgreSQL 17.x, modern Docker/Compose, GitHub Actions. Frontend scaffold requires dependency installation and lockfile generation; never claim `npm run build` passed unless actually run. SQL migrations require disposable *real* PG, not SQLite.

## Local commands
```bash
python -m venv .venv && source .venv/bin/activate
python -m pip install -r apps/api/requirements-dev.txt
PYTHONPATH=apps/api python -m pytest -q tests/bootstrap
python scripts/check_contract.py
PYTHONPATH=apps/api uvicorn roboport_api.main:app --host 127.0.0.1 --port 8000
# separate shell, after lockfile generation
cd apps/web && npm install && npm run build
```

Do not expose `0.0.0.0` unless explicitly authorized for dedicated test environment. `GET /health/live` only indicates process health; `GET /health/ready` must be 503 until real DB+IdP wiring is complete. Example env `.env.example` has no real URLs, credentials or tokens.

## CI contract
Checkout, setup Python 3.13, install API dev requirements, run Python tests, static spec; Node 24 setup and `npm ci` once package-lock tracked, `npm run build` and typecheck; report each job as run or blocked. Protected branch, CODEOWNERS and PR review. Lock dependencies, avoid latest-floating runtime upgrades. Secret scan and software supply chain checks before any deployments. No CI touching organization production secrets or customer data.

## Future services
Compose for disposable local PG17+MinIO+Keycloak only as follow-up, with random local secrets and no exposed public ports. Need integration tests for API migrations, signed URL & JWT. Image tags pinned by digest for release; not a prerequisite for Sprint 0.
