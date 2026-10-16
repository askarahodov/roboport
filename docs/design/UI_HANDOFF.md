# UI brand handoff — ROBOport (synthetic development)

- Source of truth: `design/tokens/theme.json` and matching `design/tokens/theme.css`.
- Canonical C Industrial Lab angular R symbol: keep original three SVG paths. Do not replace with a hexagon.
- Teal `accent` is decorative; interactive buttons and text use `action`/`actionText`.
- Page foreground/background, surface, error and safety token pairs are WCAG AA checked by `python scripts/check_brand_tokens.py`.
- Light/Dark/Auto use the existing theme switch. Auto responds to OS color-scheme.
- Focus for interactive controls: visible 3px outline; keyboard navigation requires no mouse.
- Browser CI evidence: `apps/web/e2e/brand-accessibility.spec.ts` tests desktop and 390px in all themes and captures six screenshots as CI artifacts.
- QA handoff: visually inspect all six screenshots for truncation/contrast, plus logged-out and authenticated views; run real keyboard Tab/Shift+Tab and focus review on a local synthetic-only environment.
- **Out of scope / release gate:** print proof, trademark rights, OEM logos, production identity, and any real customer intake must be cleared separately. E2E tests and screenshots are not a substitute for independent design/accessibility approval.
