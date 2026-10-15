# Architectural Decision Register (implementation baseline)

| ADR | Decision | State | Reason / consequences |
|---|---|---|---|
| ADR-021 | FastAPI+Python 3.13 modular monolith | **BASELINE** | reuse testable PoC; no NestJS dual-stack |
| ADR-022 | React 19+TS+Vite 8 single SPA, router areas `/`, `/account`, `/staff` | **BASELINE** | shared design, local auth PKCE; SEO/SSR later ADR |
| ADR-023 | PostgreSQL 17, SQLAlchemy 2.0, Alembic migrations | **BASELINE** | ACID; real migration and rollback tests mandatory |
| ADR-024 | external Keycloak-compatible OIDC + claims verification; no local passwords | **BASELINE** | issuer/aud/expiry/JWKS, MFA; client+staff auth isolated |
| ADR-025 | private S3 store + attachment metadata + short read TTL | **BASELINE** | deny bucket access, audit reads, infection/quarantine |
| ADR-026 | durable idempotency ledger + transactional outbox | **BASELINE** | atomic with mutation; concurrency/restart tests |
| ADR-027 | exact SKU+revision service whitelist; safety hold+two-person review | **BASELINE** | no inferred hardware compatibility |
| ADR-028 | original C angular R, muted A teal, Light/Dark/Auto | **DESIGN BASELINE** | owner-selected, legal/public approval pending |
| ADR-029 | no public launch / real customer data before legal/security/business gates | **ACTIVE GATE** | development can start on synthetic fixtures |

Early docs mentioned NestJS/Next.js as alternatives. They are **superseded for initial implementation by ADR-021/022**. Implementing a different framework requires a new ADR, tests and owner review. Existing OpenAPI 3.1.2 remains draft. Preliminary performance/availability numbers are engineering targets, not a signed SLA.

Further decisions pending: identity provider tenant strategy, web SEO solution, family accounts/B2B delegation, document retention and digital consent legal validity, print color profiles, trademark clearance, real deployment architecture, CI security scanner choices, payment support after MVP. Record these as separate issues and do not implement speculative complexity.
