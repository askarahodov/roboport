#!/usr/bin/env python3
"""Проверка локальных ссылок и якорей Markdown без сетевых запросов.

Запускается из любого каталога: python scripts/check_docs_links.py
Внешние URL не проверяются; публикация и UX документации — отдельный gate.
"""
from __future__ import annotations

import argparse
from collections import Counter
import html
from pathlib import Path
import re
from urllib.parse import unquote, urlsplit


LINK = re.compile(r"!?(?:\[[^\]\n]*\])\(\s*(<[^>]*>|[^)\s]+)", flags=re.UNICODE)
HEADING = re.compile(r"^ {0,3}#{1,6}\s+(.+?)\s*#*\s*$")
EXPLICIT_ID = re.compile(r"""\bid\s*=\s*["']([^"']+)["']""")
CODE_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
INLINE_CODE = re.compile(r"(`+)(.*?)\1")
EXTERNAL_SCHEMES = {"http", "https", "mailto", "tel", "data", "app", "sandbox"}
DOC_NAMES = ("README.md", "AGENTS.md", "TECHNICAL_SPEC.md", "CONTRIBUTING.md", "SECURITY.md")


def without_code(text: str) -> str:
    """Убирает содержимое кодовых блоков/комментариев, сохраняя номера строк."""
    text = re.sub(r"<!--.*?-->", lambda m: "\n" * m.group(0).count("\n"), text, flags=re.S)
    cleaned: list[str] = []
    fence: str | None = None
    for line in text.splitlines(keepends=True):
        marker = CODE_FENCE.match(line)
        if marker:
            marks = marker.group(1)
            if fence is None:
                fence = marks
            elif marks[0] == fence[0] and len(marks) >= len(fence):
                fence = None
            cleaned.append("\n" if line.endswith("\n") else "")
        elif fence:
            cleaned.append("\n" if line.endswith("\n") else "")
        else:
            cleaned.append(INLINE_CODE.sub("", line))
    return "".join(cleaned)


def github_slug(heading: str) -> str:
    heading = html.unescape(heading)
    heading = re.sub(r"!\[([^]]*)\]\([^)]*\)", r"\1", heading)
    heading = re.sub(r"\[([^]]*)\]\([^)]*\)", r"\1", heading)
    heading = re.sub(r"<[^>]+>", "", heading)
    heading = re.sub(r"[*_`~]", "", heading).strip().lower()
    # GitHub оставляет Unicode-буквы, цифры, подчёркивания, пробелы и дефисы.
    heading = re.sub(r"[^\w\- ]", "", heading, flags=re.UNICODE)
    return heading.replace(" ", "-")


def heading_anchors(content: str) -> set[str]:
    text = without_code(content)
    anchors: set[str] = set()
    counts: Counter[str] = Counter()
    for line in text.splitlines():
        match = HEADING.match(line)
        if match:
            slug = github_slug(match.group(1))
            if slug:
                count = counts[slug]
                anchors.add(slug if count == 0 else f"{slug}-{count}")
                counts[slug] += 1
        anchors.update(EXPLICIT_ID.findall(line))
    return anchors


def iter_markdown(root: Path) -> list[Path]:
    files = [root / n for n in DOC_NAMES if (root / n).is_file()]
    for dirname in ("docs", "infra"):
        folder = root / dirname
        if folder.is_dir():
            files.extend(folder.rglob("*.md"))
    return sorted(set(files))


def check_links(root: Path, sources: list[Path] | None = None) -> list[str]:
    root = root.resolve()
    issues: list[str] = []
    cache: dict[Path, set[str]] = {}
    for source in sources if sources is not None else iter_markdown(root):
        source = source.resolve()
        content = source.read_text(encoding="utf-8")
        text = without_code(content)
        for match in LINK.finditer(text):
            raw = match.group(1).strip("<>")
            try:
                url = urlsplit(raw)
            except ValueError:
                issues.append(f"{source.relative_to(root)}: некорректная ссылка {raw!r}")
                continue
            if url.scheme.lower() in EXTERNAL_SCHEMES or raw.startswith("//"):
                continue
            if url.scheme:  # Неизвестная схема — невалидна, в т. ч. javascript:
                issues.append(f"{source.relative_to(root)}: недопустимая схема в ссылке {raw!r}")
                continue
            target_path = unquote(url.path)
            if target_path.startswith("/"):
                # Docsify читает /docs/_sidebar.md из собственного корня /docs/.
                # Абсолютный sidebar-route /guides/client.md — НЕ /guides в репозитории.
                if source == root / "docs" / "_sidebar.md":
                    target = root / "docs" / target_path.lstrip("/")
                    if target_path == "/":
                        target = root / "docs" / "README.md"
                else:
                    target = root / target_path.lstrip("/")
            elif not target_path:
                target = source
            else:
                target = source.parent / target_path
            target = target.resolve()
            line = text.count("\n", 0, match.start()) + 1
            location = f"{source.relative_to(root)}:{line}"
            if target != root and root not in target.parents:
                issues.append(f"{location}: выход за пределы репозитория: {raw}")
                continue
            if not target.exists():
                issues.append(f"{location}: не существует {raw} (путь {target.relative_to(root)})")
                continue
            if target.is_dir():
                candidates = (target / "README.md", target / "index.md", target / "INDEX.md")
                actual = next((p for p in candidates if p.exists()), None)
                if not url.fragment:
                    continue
                if actual is None:
                    issues.append(f"{location}: якорь на каталог без Markdown index: {raw}")
                    continue
                target = actual
            if url.fragment and target.suffix.lower() == ".md":
                if target not in cache:
                    cache[target] = heading_anchors(target.read_text(encoding="utf-8"))
                anchor = unquote(url.fragment)
                if anchor not in cache[target]:
                    issues.append(f"{location}: неизвестный якорь #{anchor} в {target.relative_to(root)}")
    return issues


def main() -> int:
    parser = argparse.ArgumentParser(description="Проверка локальных Markdown-ссылок и якорей")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    problems = check_links(args.root)
    if problems:
        print(f"FAIL: найдены ошибки Markdown-ссылок ({len(problems)}):")
        print("\n".join(" - " + p for p in problems))
        return 1
    print(f"PASS: ссылки и якоря проверены в {len(iter_markdown(args.root))} Markdown-файлах")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
