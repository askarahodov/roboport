# ROBOport — rules of brand use / v0.4 DRAFT

**Identity chosen by project owner:** C Industrial Lab's **three original path** angular **R**, combined with A Precision Service's muted turquoise colors. Never substitute A's hexagon; previous generated posters with hexagon are visual explorations, **not** the approved symbol direction. The mark remains a design direction pending clearance.

## Logo and variants
- Source geometries are the three SVG paths in `brand/symbol-light.svg` / `symbol-dark.svg`. Preserve their paths, aspect ratio and negative spaces. Light on white; dark on near-black; mono when required; no gradients/rotations/shadows/recoloring of individual paths.
- Logotype “ROBOport” and descriptor “Сервис робототехники” shall remain readable at physical output size; typo “R0BO” is prohibited.
- Preliminary protected area around mark: at least `0.30 ×` total sign/symbol height in every direction; around full wordmark at least the height of the “O” glyph. This is a *draft* until proofed across signage and print.
- Draft onscreen minimum standalone mark size **32 CSS px** on desktop / mobile UI; 24px is only an experiment threshold and is **not** approved; a physical/pixel legibility test remains. Draft print minimum sign height **12 mm**; test physical proof before approval. Logo with descriptor should not be set at 12mm.
- Use vector SVG/PDF for production masters; raster exports 1×/2×/3× and PNG with transparency only for contexts that require them. Verify color output RGB for screens and CMYK by printer proof; do not equate HEX with press-ready CMYK.

## Color and accessibility
- `Light` surface `#FFFFFF`, canvas `#F3F8FA`, primary text `#102736`, brand decoration `#008C84`, small accent labels `#006D67`, button `#006D67`.
- `Dark` surface `#102731`, canvas `#071820`, text `#F3FAFC`, brand decoration `#38D9C9`, button `#11766F`.
- Safety/errors use semantic `danger`, do not reuse industrial C's red as a brand primary. Display warning, Safety hold and QC FAIL with **text + structural block**; don't encode only with red.
- Final WCAG AA contrast testing must include disabled controls, charts, focus, error text and assistive readers. Existing tests cover base pairs, **not** full accessibility.

## Do / do not
**DO:** use C original geometry; apply light/dark variants based on background; preserve whitespace and safe margins; use service-oriented Russian copy; distinguish supported vs unverified models; separate estimates by version; show safety holds.

**DON'T:** substitute hexagon A; turn the C into a cute face; stretch logo; place on visually noisy photos without sufficient contrast; claim official affiliation with robot brands without proof; claim all models supported; label synthetic estimates legally accepted; expose previous-owner documents.

## Physical touchpoints / future deliverables
- Storefront sign at actual facade size (daylight and night-lit version), interior navigation/acceptance area, name badge and clothing, business card, job sheet, acceptance receipt, estimate form, service act, guarantee explanation, device/box label with QR, packaging, presentation passport.
- Every print master requires real legal entity details, trim size, bleed, safe zones, production file format, image license check and owner approval. **No print authorizations, spending or vendor outreach** performed.

## UI cross-product handoff
Site, customer portal and CRM share semantic token keys; CRM may have denser tables but no other mark. Export favicon, wordmark, icon library, CSS tokens and product examples under `brand/` and `tokens/`. Version changes must be explicit, with screenshot regression on both themes. Read `UX_ERROR_AND_CRM_STATES.md` for restricted flows.

## Pending approvals
Legal trademark/domain/image/font review; competitor review; owner signature on full brandbook; printer proof; independent WCAG/UX audit; integration contract. The current guide is **not a final signed brandbook**.
