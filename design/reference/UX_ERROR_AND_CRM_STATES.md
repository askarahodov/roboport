# ROBOport — exceptional states and staff workflows (v0.4)

## Customer error-state matrix

| Key | UI meaning | Secure behavior required in real backend |
|---|---|---|
| empty | no tickets/devices | show helpful empty state, do not auto-create links to foreign history |
| loading | loading permissions | no flash of private content before authorization |
| 401 | session expired | repeat login; clear private cache, no access until verified identity |
| 403 | missing document permission | conceal document contents, metadata and owners |
| 404 | foreign/missing ticket | do not enable order enumeration by IDs |
| 409 | stale estimate | require latest SENT version, do not silently carry earlier consent |
| safety | dangerous device | stop unsafe operations until engineer permit |
| revoked | owner/account link revoked | no private history even after theme toggle or retry |

All controls are demonstrative only. `data-state` selectors simulate visual states, not actual HTTP requests. Error messages must not include foreign PII, full serials or secret status tokens.

## Staff CRM seven view-only workflows
`queue` triages; `intake` records inspected hardware; `diagnostic` blocks unsafe testing; `estimates` versioning; `stock` prohibits unverified part substitution; `qc` forbids issuing QC FAIL as good; `audit` shows append-only intention. Future RBAC must be enforced server-side; this UI is not a security control.

## Acceptance criteria for current deliverable
- C mark 3 paths preserved verbatim in all standard variants.
- Light/Dark/Auto across all new screens; theme alone never changes permissions or state.
- Eight state switches select one panel exclusively; all remain legible at 390px width.
- Seven CRM tabs select one staff panel exclusively; Safety hold and QC FAIL are explicit.
- No real approvals, document downloads, stock reserves, order creation or OEM association claims.
- Content standalone, no fetch/network or external JS/CSS/image URL.

## Pending next gate
Independent UX study with participants, complete WCAG2.2 AA and screenreader verification, all production states, full CRM workflows, legal clearance, actual PostgreSQL & OIDC backend isolation tests.
