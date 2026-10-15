# HTTP API implementation policy

`contracts/openapi.yaml` (v0.10 draft) covers 42 operations, 56 schemas, error `{code,message,requestId}`. There are 39 paths. It is *a starting contract* and must be updated as implementation decisions are approved. The historic FastAPI `contract_app.py` was synthetic only; do not copy a hard-coded credential map or call 14 mocked routes production-ready.

## Universal rules
- Prefix `/api/v1`; ensure `servers.url` doesn't duplicate `/api/v1` on client URL join.
- Authentication from verifiable OIDC principal only; policy at use-case layer, not solely FastAPI router dependency.
- `GET`: read models with stable cursor + max page length; no unrestricted PII. `POST/PATCH`: `Idempotency-Key` for defined mutation endpoints, body fingerprint, durable atomic response, 409 on key conflict. Handle exact target resource state with version/ETag or `expectedVersion`.
- Errors: `{code:string,message:string,requestId:UUID}`, `X-Request-ID`, `Cache-Control:no-store` for private/error, `Referrer-Policy:no-referrer`; no stacktrace/PII in client message. 401 auth missing/expired, 403 access, 404 unknown-or-foreign, 409 state/version, 422 input, 429 throttled, 500 generic. Use `Retry-After` where suitable.
- Documents: authorize metadata & object separately, return only short-lived handle, no fake `example.invalid` in production.
- Guest claim: `reference` + `challengeReference` becomes `pending_review`, not immediately attached; separate proof bound to subject and ticket; one-time TTL and audit.
- Estimate approval: only client ticket applicant, `SENT`, version exact, evidence server-side. No browser-controlled approved-by actor or financial total.

## Acceptance of each route
(1) OpenAPI operationId stable, (2) FastAPI route exists, (3) input + 2xx response validate against JSON Schema, (4) documented 4xx match schema, (5) object auth both allow+deny case, (6) idempotency with concurrency for mutable, (7) audit correlation and no leaked fields, (8) serializable postcondition in PostgreSQL when relevant.

## Implementation order (API vertical slices)
`catalog public` → `staff ticket/create/intake` → `diagnosis/safety/FSM` → `estimate send/approve/decline` → `stock reserve/release` → `QC/handover` → `customer me/devices/tickets/documents` → `guest claim/notifications` → reports/admin.

## Contract verification
`python scripts/check_contract.py` performs local structural assertions; use dedicated third-party OAS validator, codegen and SDK test after dependencies installed. Do not confuse internal YAML parsing with independent OpenAPI conformance.
