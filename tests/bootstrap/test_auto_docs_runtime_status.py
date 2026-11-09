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
