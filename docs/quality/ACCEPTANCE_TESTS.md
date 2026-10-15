# Acceptance & QA matrix

All cases use synthetic A/B clients and controlled IDs. PASS requires actual execution; mock-only or skipped is NOT pass.

| ID | Level | Acceptance / failure case |
|---|---|---|
| AC-001 | API | catalog CANDIDATE not shown as supported; unknown model 404 |
| AC-002 | DB | exact variant SKU+region+revision and dock enforced |
| AC-003 | DB | service capability demands two distinct approval staff |
| AC-004 | DB | negative stock and parallel overreserve never succeed |
| AC-005 | FSM | invalid transition 409; safety hold no work; QC FAIL no handover |
| AC-006 | Estimate | SENT immutable; total=sum(lines); new price → new version and consent |
| AC-007 | Security | client A cannot access B's ticket/attachment even if ID known |
| AC-008 | Security | new owner B cannot read A's prior ticket/attachments |
| AC-009 | Security | revoked/expired subject denied including Idempotency replay |
| AC-010 | Auth | issuer/audience/exp/PKCE/JWKS verified in real dev IdP |
| AC-011 | Claims | phone/serial/reference insufficient; proof+review required |
| AC-012 | Docs | private blob, TTL link, access audit, quarantine restriction |
| AC-013 | UI | Light/Dark/Auto, original angular R C, 390px and keyboard |
| AC-014 | UI | loading/empty/401/403/404/409/Safety hold/revoked all localized |
| AC-015 | API | errors match Error schema 401/403/404/409/422/429/500, no leaked customer data |
| AC-016 | Replay | same key+request yields one effect; changed payload 409; two workers/restart |
| AC-017 | DB | migrations upgrade/downgrade fresh disposable PG; backup restored |
| AC-018 | Event | outbox atomic with estimate/state mutation, retry idempotent |
| AC-019 | QA | CI pytest/type/lint/contract and Playwright against installed versions |
| AC-020 | Safety | humanoid requests never enter active service workflow |

## Gates
- **S0**: bootstrap health/meta and `pytest` green; frontend build only after dependency installation.
- **S1**: actually executed PostgreSQL 17 migration smoke + concurrency; absence of postgres is blocker, never PASS.
- **S2**: real OIDC dev realm and two accounts, pen-test negative rights; fixture auth alone not enough.
- **S3–S8**: relevant AC above green for each slice, no business mocks masked as real backend.
- **Pre-release**: independent security, privacy, trademark, print and economic gates.

## Command contract
`PYTHONPATH=apps/api pytest -q tests/bootstrap`; `python scripts/check_contract.py`; `npm ci && npm run build` in `apps/web` after generating `package-lock.json`; `pytest -q tests/integration` on disposable PG only when provisioned. CI report must list executed commands, exit codes, skipped, blockers and real artifact links.
