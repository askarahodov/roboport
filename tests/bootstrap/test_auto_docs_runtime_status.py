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
