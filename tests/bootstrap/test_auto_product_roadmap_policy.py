"""Защита целевой автомобильной концепции от регресса к robot-first roadmap."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_automotive_matrix_has_complete_unique_feature_catalog():
    matrix = _read("docs/auto/FUNCTIONAL_MATRIX.md")
    ids = re.findall(r"^\|\s*([A-E]\d{2})\s*\|", matrix, flags=re.MULTILINE)
    expected = (
        [f"A{i:02}" for i in range(1, 11)]
        + [f"B{i:02}" for i in range(1, 12)]
        + [f"C{i:02}" for i in range(1, 11)]
        + [f"D{i:02}" for i in range(1, 13)]
        + [f"E{i:02}" for i in range(1, 10)]
    )
    assert ids == expected
    assert len(ids) == len(set(ids)) == 52
    for marker in ("current-dev", "legacy", "planned", "M1", "M2", "M3"):
        assert marker in matrix
    assert "autodealer.ru/online/help/o-programme.html" in matrix
    assert "настольной версии" in matrix
    assert "другой вариант продукта" in matrix


def test_full_product_end_state_and_migration_not_robot_plus_auto_tab():
    readme = _read("README.md")
    assert "Airmatik Pro Service — система управления автосервисом" in readme.splitlines()[0]
    assert "Единая система независимого мультибрендового сервиса домашних роботов" not in readme
    product = _read("docs/auto/PRODUCT.md")
    architecture = _read("docs/auto/ADR-001.md")
    roadmap = _read("docs/auto/FEATURE_ROADMAP.md")
    for text in (readme, product, architecture, roadmap):
        assert "#165" in text
        assert "автомобил" in text.lower()
    for word in ("Запись", "заказ-наряд", "склад", "CRM", "деньги"):
        assert word.lower() in product.lower()
    assert "обязательной задачей" in architecture
    assert "Нельзя автоматически переносить" in architecture


def test_roadmap_tracks_all_new_business_verticals_and_release_boundary():
    roadmap = _read("docs/auto/FEATURE_ROADMAP.md")
    for issue in range(165, 175):
        assert f"#{issue}" in roadmap
    for issue in ("#119", "#112", "#113", "#114", "#115", "#117", "#121"):
        assert issue in roadmap
    assert "current-dev" in roadmap
    assert "платеж" in roadmap.lower() or "деньги" in roadmap.lower()
    assert "backup/restore" in roadmap


def test_docsify_navigation_and_agents_reference_automotive_roadmap():
    sidebar = _read("docs/_sidebar.md")
    index = _read("docs/INDEX.md")
    agents = _read("AGENTS.md")
    for text in (sidebar, index, agents):
        assert "FEATURE_ROADMAP.md" in text
    assert "/auto/FEATURE_ROADMAP.md" in sidebar
    assert "robot-таблицы" in agents
    assert "LEGACY" not in sidebar or "legacy" in agents.lower()
