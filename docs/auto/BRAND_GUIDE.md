# БоксРитм / Auto Digital — действующий бренд-гайд
**Версия:** 1.0 · **Дата:** 09.11.2026 · **Статус:** действующая идентичность закрытого synthetic dev; **не** разрешение на публичную эксплуатацию или правовая регистрация (#119).

## Название и применение
**БоксРитм — цифровая система управления автосервисом.** «Бокс» — место технического обслуживания, «Ритм» — управляемый поток записей, заказов, запчастей и контроля. В русском интерфейсе всегда «БоксРитм» (одним словом, с прописными Б/Р); «Auto Digital» — обозначение графического направления. Технические namespace, git repo `roboport`, `ROBOport_*` переменные и исторические robot материалы остаются без миграции.

## Векторные исходники и логика символа
**Каноническая геометрия (128×128):** внешний контур сервисного бокса, автомобильный силуэт, цифровая линия ритма; три воспроизводимых SVG path. Master [light](../../design/brand/symbol-light.svg), [dark](../../design/brand/symbol-dark.svg), [mono black](../../design/brand/symbol-mono-black.svg), [mono white](../../design/brand/symbol-mono-white.svg). Компактные web icon [light](../../apps/web/public/logo-light.svg), [dark](../../apps/web/public/logo-dark.svg), [favicon](../../apps/web/public/favicon.svg) используют ту же геометрию. Горизонтальные [light](../../design/brand/wordmark-light.svg), [dark](../../design/brand/wordmark-dark.svg), [mono](../../design/brand/wordmark-mono.svg) — редактируемый SVG с system font fallback; [бренд-борд](../../design/brand/brandboard.svg) показывает контраст.

**Охранное поле:** не менее 16 единиц при viewBox 128 или 0.125 высоты с каждой стороны; вокруг полного wordmark — не менее высоты строчной буквы. **Минимум:** компактный знак 32 CSS px, полный wordmark 240 CSS px. Не растягивать, не вращать, не добавлять чужой OEM или знак прежней R. Для маленьких носителей использовать icon-only; встраивать SVG как локальный asset, не по CDN. SVG text зависит от доступного шрифта; для полиграфии после согласования нужно перевести его в кривые, проверить кириллицу и лицензию.

## Semantic токены и контраст
Единственный machine-readable источник: [theme.json](../../design/tokens/theme.json); соответствующий [theme.css](../../design/tokens/theme.css) служит React. Светлая/тёмная/системная тема. Auto следует `prefers-color-scheme`, не влияет на сессии и права.

| Роль | Light | Dark |
|---|---|---|
| background / surface | `#F6F8FC` / `#FFFFFF` | `#0B0E14` / `#161D2B` |
| foreground | `#0B0E14` | `#F5F8FF` |
| accent / action | `#0066FF` | `#5CA9FF` |
| actionText | `#FFFFFF` | `#0B0E14` |
| focus | `#0054D9` | `#8EC5FF` |
| errorText / errorSurface | `#B4233A` / `#FFF1F3` | `#FFD2D9` / `#451C29` |
| safetyText / safetySurface | `#8B4500` / `#FFF3E7` | `#FFE0AA` / `#38260F` |

Иные роли: muted, border, successText/successSurface, disabledText/disabledSurface. Все обычные пары текста (body, action, error, safety, success, muted, disabled) проверяются WCAG AA ≥4.5:1, focus/background ≥3:1. **Не** применять `#8A94A6` или вторичный `#5CA9FF` как мелкий текст на белом без отдельного расчёта. Цвет не единственный индикатор статуса: 401/403/404, Safety Hold, QC FAIL, 409 всегда сопровождаются текстом/иконкой.

## Типографика и UX
Стек: `Manrope, ui-sans-serif, system-ui, Arial, sans-serif`. **Manrope пока не поставляется в поставке:** до проверки лицензии и локальной кириллической версии без CDN действует системный sans fallback. H1 ExtraBold 32–40+, H2 Bold 20–24, лейблы 12–14 Semibold, body 14–16 (масштаб 200% без обрезки). Контролы имеют видимый `:focus-visible`, высоту интерактивной области от 44px, различимые disabled, error/warning/success, мобильные меню с горизонтальным внутренним прокручиванием, таблицы без переполнения root.

## Применение и запреты
Использовать logo и название в шапке, футере, входе, staff и client, Docsify, title/favicon и серверной метаинформации. Не менять технические роуты и формат событий без версионированной миграции. Исторические документы [robot preliminary](../design/PRELIMINARY_BRANDBOOK.md) и [robot C rulebook](../../design/reference/BRANDBOOK_RULES_V04.md) **архивные**; не переименовывать прошлые PR/evidence, не выдавать старые SVG за нынешний бренд. Не заявлять регистрацию знака, авторизацию OEM или успешный production rollout.

## Проверки и sign-off
`python scripts/check_brand_tokens.py` проверяет SVG geometry, наличие русских названий в текущем UI/API/Docsify и token roles/contrast. `apps/web/e2e/brand-accessibility.spec.ts` и `visual-audit.spec.ts` — 390/1280/1440 Light/Dark/Auto, фокус, root overflow и снимки. `docs/guides/SCREENSHOT_EVIDENCE.md` — ролевые Docsify снимки. Все 5 PR+main CI jobs на точных SHA. **Ручная QA/независимая визуальная и юридическая приёмка пока не проводятся**, локальные ИИ-агенты отключены по решению владельца. Отдельный #92/#76 review не отмечать PASS; внешний выпуск запрещён #119.
