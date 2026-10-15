# Issue / agent task template

## RP-NNN — Outcome
**State:** TODO / IN_PROGRESS / BLOCKED / VERIFIED. **Depends:** RP-NNN. **Role:** Agent specialization, independent TESTER mandatory for risky changes.

**Business behavior** — precise observable user scenario, input and output.
**Files to edit** — exact bounded paths, no unrelated architecture changes.
**Preconditions** — data model/ADR/contract and available dependencies.
**Functional AC** — happy path, negative path, role/ownership, error schema.
**Nonfunctional AC** — audit, idempotency, WCAG, performance, localization.
**Test plan** — exact commands; distinguish unit, mocked, integration, browser, external.
**Evidence** — actual exit codes, test count and screenshots or SQL log.
**Risks & rollback** — migrations / data / public behavior.
**Ready-to-merge** — all required checks green; otherwise BLOCKED with reason.
