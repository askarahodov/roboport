"""Проверка честного статуса автомобильного продукта в корневом README.

Защищает от возврата к устаревшему «всё planned» и от объявления
synthetic/current-dev функций готовыми к production.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_readme_automotive_current_dev_status_tracks_verified_verticals():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    rows = {
        line.split("|")[1].strip(): line
        for line in readme.splitlines()
        if line.startswith("| ") and line.count("|") >= 4
    }
    for module in (
        "Организации / филиалы",
        "Запись / календарь",
        "Заказ-наряды",
        "Запчасти / склад",
        "Деньги / документы",
        "Клиентский кабинет",
    ):
        assert module in rows, module
        assert "`current-dev`" in rows[module], module

    for module in ("CRM / связь", "Отчёты"):
        assert module in rows, module
        assert "`planned`" in rows[module], module


def test_readme_current_dev_never_means_production_approval():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for requirement in (
        "не готовый автосервис для production",
        "закрытом синтетическом dev-контуре",
        "security/legal/ops approvals",
        "gate #119",
        "docs/auto/FEATURE_ROADMAP.md",
        "docs/guides/ACCESS_AND_TEST_MATRIX.md",
        "legacy",
    ):
        assert requirement in readme, requirement
