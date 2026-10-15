# Security & privacy model (development baseline)

## Authentication
External OIDC; SPA uses Authorization Code + PKCE, backend verifies JWT or introspection by IdP policy: exact issuer, audience, signature with JWKS refresh, exp/nbf, nonce/CSRF as appropriate, roles/scopes. Never accept `X-Customer-ID` as proof. Test in dev realm with SYNTHETIC-A and SYNTHETIC-B; no login in production until configuration and threat model approved.

## Authorization
| Resource | CLIENT | INTAKE | ENGINEER | STOCK | MANAGER | FINANCE | ADMIN | AUDITOR |
|---|---|---|---|---|---|---|---|---|
| ticket | own applicant only | assigned | assigned diagnosis | no PII | supervised | financial fields only | explicit audited breakglass | redacted trail |
| device/history | current ownership metadata; prior owner's private history DENY | intake scope | assigned | inventory-only | supervised | none | restricted audited | redacted |
| estimate | own immutable sent version+approve | create draft | proposed lines | parts only | send, review | money fields | never fake customer proof | trail |
| documents | own ticket & permitted attachment only | process scope | assigned | no customer PII | process scope | invoice subset | breakglass only | metadata |
| stock reservation | none | none | request | execute within approved compatibility | override only with second approval | read cost | infra only | read trail |
| service safety | read customer-safe status | flag | flag & qualified assessment | stop reserve | second reviewer | none | cannot solo bypass | audit |

RBAC alone is insufficient: **ABAC** validates ticket applicant, device ownership at time, attachment ticket ID, link revoked_at, estimate version/status, assignment, service capability, staff eligibility and data class. A `404` for unauthorized foreign object prevents existence probing when warranted. Sensitive fields are filtered server-side.

## High-risk threat scenarios / required tests
- IDOR: A tries ticket/document B, and a new owner tries previous owner order/doc.
- Broken authenticated flow: forged customer header, expired/revoked session, screenshot URL, stale signed document link.
- Account takeover: guest claim with only order #, phone or serial, repeated proofs; rate-limit, independent verification and staff review.
- Duplicate mutations: same Idempotency-Key and changed payload 409, same request replay identical, concurrent two workers, after revocation denied, after restart persisted.
- Physical safety: unauthorized engineer / missing exact part / safety hold bypass / QC FAIL handover.
- Audit & storage: append-only ACL, no secrets in logs, no public bucket, malware/quarantine.

## Data classifications
PUBLIC (published catalog), INTERNAL (methods/stock operational), PII (people + ticket linkage), RESTRICTED (documents, tokens, proofs). Client UI sees explicit allowlisted view models only. Data retention and consent policy subject to jurisdiction; dev fixtures must not contain real phone/email/address/serial. Project repo is **public**. Treat GitHub Issues/PR comments as public data too.

## Release blockers
Trademark/OEM rights, customer data lawful basis/consent, independent pentest, TLS setup, IAM review, privacy impact, backup restore, monitoring and on-call, security incident process, economic go/no-go. These do not prohibit synthetic local development.
