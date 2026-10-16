# RP-007/008 acceptance evidence — synthetic catalog and ticket FSM

The source of truth for legal states and transitions is `docs/domain/TICKET_TRANSITIONS.json`; the executable PostgreSQL FSM is `apps/api/roboport_api/ticket_fsm.py`. The earlier statement in `TICKET_FSM.md` that the database and service are not implemented is obsolete.

| Acceptance scenario | Executable regression |
|---|---|
| Exact variant including dock, region, revision; no implicit match | `tests/integration/test_pg_catalog_capability.py::test_catalog_exact_variant_dock_region_revision_and_two_reviewers` |
| Candidate remains unsupported until two distinct qualified reviews | `test_pg_catalog_capability.py` approval / negative DB-trigger cases |
| Unverified / invalid part cannot become SUPPORTED by direct DB update | `test_pg_catalog_capability.py::test_part_compatibility_cannot_be_supported_without_independent_evidence` |
| HTTP role, CSRF, idempotency and revoke | `test_pg_catalog_capability.py::test_catalog_http_exact_and_live_review` |
| Staff intake state-event, audit immutability and optimistic conflict | `tests/integration/test_pg_ticket_fsm.py::test_staff_intake_transition_version_audit_and_safety_bundle` |
| Concurrent state updates (one winner, one conflict) | `test_pg_ticket_fsm.py::test_concurrent_version_conflict_does_not_duplicate_events` |
| Independent safety experts and safety hold release | `test_pg_ticket_fsm.py::test_independent_safety_expert_release_and_revoke` |
| QC FAIL and Safety Hold prohibit unsafe transition | `test_pg_ticket_fsm.py::test_safety_and_qc_guard_deny_illegal_shortcuts` |
| Generic PATCH/PUT of status denied; unauthenticated review denied | `tests/bootstrap/test_variant_fsm_boundary.py` |

Run the entire integration suite against disposable PostgreSQL 17, not against a real customer DB. CI also runs contract and browser tests. A passing code gate is not an independent safety certification, a supported physical robot SKU, or permission to accept real service requests. Human acceptance stays pending until the synthetic local test stage.
