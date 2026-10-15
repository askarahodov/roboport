# Sprint 0 — first implementation checkpoint (start immediately)

Objective: repo becomes reproducible, AI agents can independently run tests and understand the product. No public deployment or real-data operations.

1. RP-001 `✅ initial source package`: working SPEC, docs, original design tokens & C logo, synthetic OpenAPI draft, AGENTS and ignore rules. Verify no secrets before push.
2. RP-002 `✅ bootstrap API source`: FastAPI /health/live, /health/ready (503 before infrastructure), /api/v1/meta; 401 on unimplemented private routes; pytest tests.
3. RP-003 `SCAFFOLD ONLY`: React/TS Vite with theme toggle and placeholder route; npm dependencies not installed in this environment; generate lockfile and actually run build on machine with registry access, fix failures.
4. RP-004 `STRUCTURAL ONLY`: parse existing OpenAPI, count 42 ops/56 schemas, check route collision; independent OAS lint/codegen later.
5. RP-018 `WORKING DESIGN BASELINE`: original angular R C, A teal Light/Dark/Auto, written preliminary brandbook and synthetic demo; legal/print not approved.
6. Next execution order: fix CI/build; real PG17 integration + Alembic fresh baseline; dev OIDC with two synthetic subjects; then ticket/catalog vertical slice. Archive six SQL drafts never applied blindly.

**Stop condition Sprint 0:** tests + lint green for components *actually installed*, CI reproducible; blocked frontend/install and PG called out, not claimed green.
