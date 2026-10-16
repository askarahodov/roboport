# PostgreSQL 17 / Alembic acceptance ledger — RP-005

Development uses disposable PostgreSQL 17. No production DB or real customer data permitted.

| Criterion | Executable proof |
|---|---|
| Alembic upgrade→downgrade base→upgrade head | `.github/workflows/ci.yml`, postgres job: Real PostgreSQL upgrade / downgrade / re-upgrade |
| Foreign keys, state/check constraints, immutable claimant/audit | `tests/integration/test_pg_baseline.py` |
| Estimate SENT immutability | `tests/integration/test_pg_estimate_stock.py` |
| Active role / ticket ACL **partial indexes** (non-unique) | `tests/integration/test_pg_schema_integrity.py` |
| Exact catalog variant uniqueness and capability FK | `tests/integration/test_pg_schema_integrity.py` |
| Concurrent stock reserve | `tests/integration/test_pg_inventory.py` |
| Competing quote versions and ticket operations | `tests/integration/test_pg_cross_operation_race.py` |
| Session and account access after revoke | `tests/integration/test_pg_customer_account_http.py` |
| Real pg_dump/pg_restore plus positive/negative restored FK probe | `.github/workflows/ci.yml`: Synthetic PostgreSQL pg_dump / pg_restore roundtrip |
| Six old SQL drafts excluded from migrations | `db/legacy_drafts/` and `tests/contract/test_schema_mapping_gate.py` |

**Scope distinction:** the partial role/ACL indexes optimize live lookups; they are *not* unique constraints. Uniqueness is separately enforced for exact catalog identity and other specific domain keys. Do not claim a partial unique constraint unless one exists.

Contract and schema governance: `docs/data/SCHEMA_FIELD_MATRIX.json` and `tests/contract/test_schema_mapping_gate.py`.

Full independent acceptance requires review of the CI run links and a local synthetic-only database test; track it under QA #76 rather than blocking completed implementation issues.
