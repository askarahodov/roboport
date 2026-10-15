# ROBOport — preliminary brandbook, development baseline v1.0

**Status:** a complete *internal build guide* for day/night product design, **not** trademark/print/public sign-off. Working name ROBOport; never claim OEM authorization. Final public visual identity gate remains open.

## 1. Brand identity
Promise: calm, precise multibrand robotics diagnostics, maintenance, repair. Voice: professional, friendly, transparent about service eligibility, price, safety. Avoid neon/aggressive cyber imagery and «официальный сервис» claims without evidence. Describe only confirmed compatible models.

## 2. Wordmark and graphic sign
The owner-selected mark is the **C Industrial Lab angular R made from three original SVG paths**. `design/brand/symbol-{light,dark}.svg` and `design/brand/wordmark-*.svg` are canonical shape references. No hexagon A, no changes to contour/path proportions/negative spaces, stretch, shadow or recoloring per path. `ROBOport` text capitalization; descriptor «Сервис робототехники» readable only at supported size.

## 3. Logo variations & exclusions
Light on white/light surfaces; dark on near-black/night surfaces; mono-black/white where necessary. Do not place on low-contrast photograph, cluttered product packaging, foreign OEM badges, or imply OEM affiliation. Draft protected space: ≥0.3× symbol height; for wordmark ≥ letter O height. Draft min on-screen **32px** for standalone sign; min physical **12mm** to proof; both require legibility test before release. Export SVG/PNG 1x/2x/3x; print PDF after actual color proof, no raw HEX→CMYK assumption.

## 4. Color rules (development tokens)
| Semantic | LIGHT | DARK |
|---|---|---|
| canvas | `#F3F8FA` | `#071820` |
| surface | `#FFFFFF` | `#102731` |
| text | `#102736` | `#F3FAFC` |
| decorative accent | `#008C84` | `#38D9C9` |
| actionable primary | `#006D67` | `#11766F` |
| small accentInk | `#006D67` | `#38D9C9` |

Don't rely solely on colors: Safety hold, quality fail, estimate declined, expired session have text+icon+semantic role. Verify WCAG contrast for actual combinations, including hover/focus/disabled, not just three button swatches.

## 5. Typography, iconography, illustration
Primary in scaffolds: accessible system sans with full Cyrillic; Inter/Manrope candidates only when licenses, glyphs and embedding verified. Headline 28–40px desktop / 24–30px mobile, body 16px, captions ≥12px only where WCAG allows; typography scale and line-height tokens. Icons outlined consistent 2px weight, 20–24px, labeled for assistive technology. Illustrations of household robotics must not show unapproved repair claim or copyrighted OEM photo without permission.

## 6. Layout and interactions
4/8px spacing grid, responsive mobile-first widths (320/390/768/1280), content max 1200px, visible focus, keyboard-trappable modals, error summary, accessible labels. Common components: Button, Input, Select, Card, Badge, Table, DataGrid, Timeline, Stepper, Alert, Modal, Toast, Skeleton, EmptyState, Dialog, Document row, Estimate version panel, SafetyHold, QCFail. Text contrast target WCAG 2.2 AA. Preferred color mode Auto defaults to OS; user may override Light/Dark; only display choice is persisted, never security state.

## 7. Applied layouts
- Public: home, brands/models, eligibility, request, legal/contact, status limited.
- Client: sign-in/profile, overview, robots/history, tickets, estimate exact-version approval, docs, preferences/notifications and error states.
- CRM: queue, intake, diagnosis/Safety hold, estimates, stock, QC/handover, audit.
- Physical (design draft only): day/night signboard, reception, staff badge/uniform, business card, intake sheet, estimate, job sheet, warranty/act, QR service label, protective packaging.

## 8. Voice and microcopy examples
«Проверяем возможность обслуживания» rather than «Ремонтируем всё»; «Смета версии 2 ожидает согласования» rather than «Цена фиксирована навсегда»; «Работы приостановлены из-за проверки безопасности» rather than generic red badge. Never disclose customer B's private info in error for A.

## 9. Handoff files and gates
Master angular R SVG; theme.json/CSS; self-contained prototype; printable proof templates are **not** finished, and the reference images do not grant commercial image rights. Typography licenses, domain/trademark clearance, IP similarity, OEM image grants, CMYK proof, sign production, final owner approval and independent UX/WCAG remain *external release blockers*.
