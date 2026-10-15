# Data model — authoritative logical design before migrations

Existing six SQL drafts are **not executed** and contain assumptions, not truth. Build fresh SQLAlchemy + Alembic baseline and test against a disposable PostgreSQL 17 instance (no SQLite substitution).

| Context | Entities (core) | Invariants |
|---|---|---|
| Catalog | brand, robot_model, model_variant, dock_variant, service_operation, service_capability | SKU+region+revision; capability approval by two distinct staff; CANDIDATE not SUPPORTED |
| Identity | customer_account, account_identity, account_customer_link | unique IdP subject, verified link only; immutable verified fields, revokedAt permanent |
| Customers | customer, robot_device, device_ownership | ownership time bounds; one current owner; don't transfer previous ticket access |
| Tickets | service_ticket, intake_record, diagnosis, ticket_state_event | expected version, custody, safety holds, append-only state events |
| Estimates | estimate, estimate_line, estimate_approval | immutable SENT/APPROVED amounts+lines, approval exact version, amount equals sum(lines) |
| Inventory | part, part_compatibility, stock_lot, stock_movement, reservation | exact part number and hardware variant; unique/stock no negative; concurrent locks |
| Work/QC | worklog, quality_check, handover | qualified actor; QC FAIL prevents handover; audit reason |
| Docs | attachment, document_access_audit, quarantine_result | private bucket, malware, ticket-level permission, immutable audit |
| Comms | notification_outbox, account_notification_preference | transactionally enqueued, service vs marketing separated, retries and idempotency |
| Idempotency | idempotency_request | unique(account,key_hash), request fingerprint, IN_PROGRESS/SUCCEEDED, response_json, expires_at, actor auth rechecked |

**Required columns policy:** UUID primary identifiers; `created_at,updated_at` UTC; optimistic `version` on mutable aggregates; `created_by` and actor audit; money integer minor units with currency; `deleted_at` only with explicit retention policy; FK `ON DELETE` explicit and tested. Model references cannot be inferred from serial alone. Do not embed secrets/OTP/proofs in rows or JSON error messages. Use `CHECK`, `FK`, `UNIQUE`, partial index for active relationships; all deterministic tests seed synthetic IDs.

## Migration plan
1. Publish new Alembic `0001_core_catalog_identity_ticket`; compare expected structure with legacy 001–006 in ADR diff.
2. Add estimates & inventory `0002`; safety / status guards and append-only audit `0003`.
3. Add documents/outbox/idempotency `0004`, explicit transactional replay implementation.
4. Validate up/down on fresh disposable PG and PG minor version, `pg_dump/pg_restore` rehearsal, two connections concurrent stock reservation, stale estimate immutability, two-party service approval and REVOKED constraints.
5. Never run migrations against company infrastructure or nonempty DB without authorization.

See `docs/quality/ACCEPTANCE_TESTS.md` for SQL gate. Any nullable/constraint/transition disagreement among legacy schemas must be resolved with an ADR, not silently copied.
