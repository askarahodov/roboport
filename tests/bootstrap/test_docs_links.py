"""Проверки качества ссылок в документации на синтетических файлах."""
import importlib.util
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[2] / "scripts" / "check_docs_links.py"
SPEC = importlib.util.spec_from_file_location("docs_link_checker", MODULE_PATH)
assert SPEC and SPEC.loader
checker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(checker)


def test_links_and_unicode_heading(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (tmp_path / "README.md").write_text(
        "[Инструкция](docs/guide.md#проверка-входа)\n"
        "[Относительная](docs/guide.md)\n"
        "[Внешняя](https://example.com/example#anything)\n",
        encoding="utf-8",
    )
    (docs / "guide.md").write_text("# Проверка входа\nОписание\n", encoding="utf-8")
    assert checker.check_links(tmp_path) == []


def test_missing_file_and_wrong_anchor(tmp_path):
    (tmp_path / "README.md").write_text(
        "[Нет файла](missing.md)\n[Нет заголовка](README.md#несуществующий)\n",
        encoding="utf-8",
    )
    errors = checker.check_links(tmp_path)
    assert len(errors) == 2
    assert "не существует" in errors[0]
    assert "неизвестный якорь" in errors[1]


def test_ignores_links_in_code_and_rejects_unsafe_scheme(tmp_path):
    (tmp_path / "README.md").write_text(
        "```md\n[Пример](missing.md)\n```\n"
        "[Нельзя](javascript:alert)\n", encoding="utf-8",
    )
    issues = checker.check_links(tmp_path)
    assert len(issues) == 1
    assert "недопустимая схема" in issues[0]


def test_duplicate_headings_match_github_anchors(tmp_path):
    (tmp_path / "README.md").write_text(
        "# Повтор\n# Повтор\n[Вторая](#повтор-1)\n", encoding="utf-8",
    )
    assert checker.check_links(tmp_path) == []


def test_directory_traversal_is_rejected(tmp_path):
    (tmp_path / "README.md").write_text("[Нельзя](../secret.md)\n", encoding="utf-8")
    errors = checker.check_links(tmp_path)
    assert len(errors) == 1
    assert "выход за пределы" in errors[0]
