# OpenAPI contract verification (#15)

Contract: contracts/openapi.yaml, version 0.14.0-draft, OpenAPI 3.1.2. Reviewed operation IDs are frozen in contracts/operation_ids.json (46 declared operations).

Verification commands:

    python -m pip install -r apps/api/requirements-dev.txt -c apps/api/constraints-py313.txt
    python -m pip install openapi-spec-validator==0.7.2
    python scripts/check_contract.py
    python -m openapi_spec_validator contracts/openapi.yaml
    PYTHONPATH=apps/api python -m pytest -q tests/bootstrap tests/contract
    cd apps/web && npm ci && npm run generate:api && git diff --exit-code -- src/shared/api/schema.d.ts && npm run typecheck && npm run build

The Python gate performs independent OAS spec validation, frozen operationId checks, successful 2xx responses, canonical Error responses for declared 400/401/403/404/409/422 and a secure localSession cookie scheme. Mutation tests intentionally break ID, $ref, status, error schema and session definition.

Only three public catalog GET handlers are independently verified by HTTP tests: /api/v1/catalog/brands, /api/v1/catalog/models, /api/v1/catalog/models/{modelId}. The remaining 43 are declarations, NOT proven production implementations. Synthetic-only handlers implemented in other issues do not change this classification.

OpenAPI TypeScript codegen uses pinned openapi-typescript 7.10.1; CI requires generated schema.d.ts to match the tracked version. Python independent validator uses pinned 0.7.2 in CI. No real customer records, payments, or public launch are authorized.

A green full CI run and independent review are required for the final #15 checkpoint.
