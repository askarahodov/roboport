"""Documentation guard for automotive runtime and role navigation (#173).

This checks explicit, user-facing release boundaries rather than trying to
replace integration, browser, security or independent acceptance testing.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_docs_do_not_describe_robot_domain_as_live_application():
    guides = (ROOT / "docs/guides/INDEX.md").read_text(encoding="utf-8")
    home = (ROOT / "docs/README.md").read_text(encoding="utf-8")
    index = (ROOT / "docs/INDEX.md").read_text(encoding="utf-8")
    obsolete = "Текущее приложение продолжает обслуживать тестовую робототехническую предметную область"
    for page in (guides, home, index):
        assert obsolete not in page
        assert "#119" in page
        assert "legacy" in page.lower()
    assert "автомобильные dev API" in guides
    assert "robot sql" in guides.lower()
    assert "draft OpenAPI" in guides
    assert "robot HTTP-маршруты исключены" in home
    assert "current-dev" in index and "planned" in index


def test_guides_map_actual_synthetic_automotive_navigation():
    guides = (ROOT / "docs/guides/INDEX.md").read_text(encoding="utf-8")
    for route in (
        "/staff/auto-calendar",
        "/staff/auto-order-services",
        "/staff/auto-workshop-qc",
        "/staff/warehouse-stocktake",
        "/staff/warehouse-receiving",
        "/staff/warehouse-work-orders",
        "/account/vehicles",
        "/account/auto-booking",
        "/account/auto-orders",
    ):
        assert f"`{route}`" in guides, route
    assert "готового публичного автомобильного кабинета пока нет" in guides
    assert "не пройдены" in guides
    assert "`planned`" in guides

def test_navigator_covers_all_roles_and_keeps_nonproduction_gates():
    navigator = (ROOT / "docs/guides/ROLE_NAVIGATOR.md").read_text(encoding="utf-8")
    sidebar = (ROOT / "docs/_sidebar.md").read_text(encoding="utf-8")
    start = (ROOT / "docs/guides/QUICK_START.md").read_text(encoding="utf-8")
    for role in ("Администратор", "Приёмщик", "Механик", "Склад",
                 "Руководитель", "Бухгалтер", "Клиент", "Мобильное"):
        assert role.lower() in navigator.lower(), role
    for code in ("401", "403", "404", "409", "422", "429"):
        assert code in navigator, code
    for route in ("/account/auto-booking", "/staff/auto-calendar",
                  "/staff/auto-workshop-qc", "/staff/warehouse-work-orders"):
        assert route in navigator and route in start, route
    assert "ROLE_NAVIGATOR.md" in sidebar
    assert "releaseAllowed: false" in navigator
    assert "#119" in navigator and "#165" in navigator
    assert "production" in navigator.lower()

def test_additional_documented_staff_routes_match_live_react_router():
    """An implemented UI route must stay documented, not silently become planned."""
    react = (ROOT / "apps/web/src/main.tsx").read_text(encoding="utf-8")
    navigator = (ROOT / "docs/guides/ROLE_NAVIGATOR.md").read_text(encoding="utf-8")
    for route in (
        "/staff/auto-diagnostics",
        "/staff/auto-intake-act",
        "/staff/auto-bay-occupancy",
        "/staff/auto-demo-finance-report",
        "/staff/auto-crm-leads",
        "/staff/auto-crm-funnel",
        "/staff/auto-stock-abc-xyz",
        "/staff/warehouse-serial-movements",
        "/account/auto-intake-act",
        "/account/auto-estimates",
    ):
        assert f'<Route path="{route}"' in react, route
        assert f'`{route}`' in navigator, route
    assert "только" in navigator.lower()
    assert "releaseAllowed: false" in navigator


def test_all_concrete_automotive_react_routes_have_navigator_entries():
    """Avoid a hand-maintained allowlist silently missing future live screens."""
    import re

    router = (ROOT / "apps/web/src/main.tsx").read_text(encoding="utf-8")
    account = (ROOT / "apps/web/src/pages/customer/CustomerAccount.tsx").read_text(encoding="utf-8")
    navigator = (ROOT / "docs/guides/ROLE_NAVIGATOR.md").read_text(encoding="utf-8")
    explicit = set(re.findall(r'<Route path="([^"]+)"', router))
    nested = set(re.findall(r"path: '(/account[^']*)'", account))
    concrete = (explicit | nested) - {"*", "/account/*", "/staff/*"}
    assert len(concrete) >= 25, "Route inventory unexpectedly shrank: inspect router"
    for route in sorted(concrete):
        assert f"`{route}`" in navigator, f"Undocumented React route: {route}"
    assert "current-dev" in navigator and "planned" in navigator


def test_docs_disclose_approved_brand_transition_without_claiming_ui_cutover():
    """The approved brand must not be confused with the as-yet unchanged UI."""
    home = (ROOT / "docs/README.md").read_text(encoding="utf-8")
    navigator = (ROOT / "docs/guides/ROLE_NAVIGATOR.md").read_text(encoding="utf-8")
    for doc in (home, navigator):
        assert "БоксРитм" in doc
        assert "БоксРитм" in doc
        assert "#110" in doc
        assert "#121" in doc
        assert "ребрендинг" in doc.lower()


def test_navigator_has_clickable_and_existing_e2e_pg17_source_links():
    """Issue #173 requires source/test links, not bare filenames in code spans."""
    import re

    navigator = (ROOT / "docs/guides/ROLE_NAVIGATOR.md").read_text(encoding="utf-8")
    links = re.findall(
        r"\[([^]\n]+\.(?:spec\.ts|py))\]\("
        r"https://github\.com/askarahodov/roboport/blob/main/"
        r"(apps/web/e2e/[^)]+|tests/integration/[^)]+)\)",
        navigator,
    )
    assert len(links) >= 22, "Every referenced browser/PG17 test needs a clickable source link"
    for name, relative in links:
        assert relative.endswith("/" + name), (name, relative)
        assert (ROOT / relative).is_file(), f"Broken test reference: {relative}"
    assert not re.search(r"\x60[^\x60\n]+\.(?:spec\.ts|py)\x60", navigator), (
        "Unlinked test filename in role navigator"
    )
