"""Contract: Docsify role navigation reflects only available synthetic dev UI."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "docs"


def test_role_sidebar_separates_current_dev_from_planned():
    sidebar = (ROOT / "_sidebar.md").read_text(encoding="utf-8")
    current = sidebar.split("- **Роли: частично доступно в synthetic dev**", 1)[1].split(
        "- **Роли: функциональность планируется**", 1)[0]
    planned = sidebar.split("- **Роли: функциональность планируется**", 1)[1].split(
        "- **Реализовано в dev**", 1)[0]
    for page in ("client.md", "administrator.md", "intake.md", "warehouse.md", "manager.md", "mechanic.md"):
        assert "/guides/" + page in current
        assert "/guides/" + page not in planned
    for page in ("finance.md", "mobile.md"):
        assert "/guides/" + page in planned
        assert "/guides/" + page not in current


def test_role_matrix_links_are_real_and_scope_is_explicit():
    index = (ROOT / "guides" / "INDEX.md").read_text(encoding="utf-8")
    for page in ("administrator", "intake", "warehouse", "manager",
                 "client", "mechanic", "finance", "mobile"):
        assert f"({page}.md)" in index
        assert (ROOT / "guides" / (page + ".md")).is_file()
    for phrase in ("current-dev", "planned", "403", "409",
                   "production gate #119", "synthetic"):
        assert phrase in index


def test_global_index_does_not_mark_all_role_guides_as_planned():
    index = (ROOT / "INDEX.md").read_text(encoding="utf-8")
    line = next(line for line in index.splitlines()
                if "[Руководства по ролям](guides/INDEX.md)" in line)
    assert "current-dev / частично planned" in line


def test_manager_guide_sidebar_link_and_estimate_guide():
    sidebar=(ROOT / "_sidebar.md").read_text(encoding="utf-8")
    assert "[Руководитель: связь и сметы (dev)](/guides/manager.md)" in sidebar
    assert "[Механик: контроль качества (dev)](/guides/mechanic.md)" in sidebar
    assert "[Версии и согласование автомобильной сметы](/auto/ESTIMATES.md)" in sidebar
    guide=(ROOT / "auto" / "ESTIMATES.md").read_text(encoding="utf-8")
    for expected in ("/staff/auto-estimates", "/account/auto-estimates",
                     "APPROVED", "DECLINED", "releaseAllowed: false", "#119"):
        assert expected in guide


def test_synthetic_close_guide_and_role_sidebar():
    sidebar=(ROOT / '_sidebar.md').read_text(encoding='utf-8')
    assert '[Условная сверка и пакет перед выдачей](/auto/DEMO_CLOSE.md)' in sidebar
    guide=(ROOT / 'auto' / 'DEMO_CLOSE.md').read_text(encoding='utf-8')
    for phrase in ('/staff/auto-demo-close','/account/auto-demo-close',
                   'SIM_PAYMENT','SIM_REFUND','releaseAllowed','#119'):
        assert phrase in guide


def test_customer_order_history_guide_and_sidebar():
    sidebar=(ROOT / "_sidebar.md").read_text(encoding="utf-8")
    assert "[История заказ-нарядов клиента и сметы](/auto/CUSTOMER_HISTORY.md)" in sidebar
    guide=(ROOT / "auto" / "CUSTOMER_HISTORY.md").read_text(encoding="utf-8")
    for text in ("/account/auto-orders", "Мои заказ-наряды (dev)",
                 "Показать ещё заказ-наряды", "Согласовать последнюю версию",
                 "releaseAllowed:false", "#119"):
        assert text in guide
