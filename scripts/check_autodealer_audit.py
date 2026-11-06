#!/usr/bin/env python3
"""Offline structural contract for page-by-page AutoDealer Online gap inventory.

Never crawls an external website in CI. Checking source contents, freshness and
business acceptance requires an independent human/web review (issue #108).
"""
from __future__ import annotations

import json
from pathlib import Path
import re
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
FILE = ROOT / "docs" / "auto" / "AUTODEALER_ONLINE_PAGES.json"
MATRIX = ROOT / "docs" / "auto" / "FUNCTIONAL_MATRIX.md"
EXPECTED_FIELDS = {
    "id", "url", "title", "section", "matrixId", "priority", "issue",
    "status", "scenario", "gap", "evidence", "checkedOn",
}
VALID_STATUSES = {"D_PARTIAL", "PLANNED"}
VALID_EVIDENCE = {"page", "search_excerpt"}


def validate(document: object, matrix_text: str) -> list[str]:
    problems: list[str] = []
    if not isinstance(document, dict):
        return ["root must be a JSON object"]
    if set(document) != {"schemaVersion", "checkedOn", "source",
                         "completeness", "records"}:
        problems.append("unexpected or missing top-level keys")
    if document.get("schemaVersion") != 1:
        problems.append("unsupported schemaVersion")
    if document.get("completeness") is None or (
        not isinstance(document.get("completeness"), str)
        or "PARTIAL" not in document["completeness"]
    ):
        problems.append("audit must explicitly report incomplete navigation")
    if not isinstance(document.get("checkedOn"), str) or not re.fullmatch(
        r"20\d{2}-\d{2}-\d{2}", document["checkedOn"]
    ):
        problems.append("missing or invalid checkedOn")
    records = document.get("records")
    if not isinstance(records, list):
        return problems + ["records must be an array"]
    if len(records) < 25:
        problems.append("verified coverage must not silently shrink")
    urls: set[str] = set()
    ids: set[str] = set()
    sections: set[str] = set()
    for number, row in enumerate(records, 1):
        where = f"records[{number}]"
        if not isinstance(row, dict) or set(row) != EXPECTED_FIELDS:
            problems.append(f"{where}: missing, extra or malformed fields")
            continue
        ident = row["id"]
        if not isinstance(ident, str) or not re.fullmatch(r"ADP-\d{3}", ident):
            problems.append(f"{where}: invalid ID")
        elif ident in ids:
            problems.append(f"{where}: duplicate ID")
        else:
            ids.add(ident)
        url = row["url"]
        if not isinstance(url, str):
            problems.append(f"{where}: invalid URL type")
        else:
            parsed = urlparse(url)
            if (parsed.scheme != "https" or parsed.netloc != "autodealer.ru"
                or not parsed.path.startswith("/online/help/")
                or not parsed.path.endswith(".html")
                and not parsed.path.endswith("/index.html")
                or parsed.query or parsed.fragment or parsed.params
                or ".." in parsed.path):
                problems.append(f"{where}: URL must be an Online help article")
            if url in urls:
                problems.append(f"{where}: duplicate URL")
            urls.add(url)
        matrix_id = row["matrixId"]
        if (not isinstance(matrix_id, str)
            or not re.fullmatch(r"[A-E]\d{2}", matrix_id)
            or not re.search(r"^\| " + re.escape(matrix_id) + r" \|", matrix_text, re.M)):
            problems.append(f"{where}: unknown functional matrix ID")
        if row["priority"] not in {"M1", "M2", "M3"}:
            problems.append(f"{where}: invalid priority")
        if type(row["issue"]) is not int or row["issue"] < 1:
            problems.append(f"{where}: missing GitHub owner issue")
        if row["status"] not in VALID_STATUSES:
            problems.append(f"{where}: cannot claim unverified production status")
        if row["evidence"] not in VALID_EVIDENCE:
            problems.append(f"{where}: invalid evidence provenance")
        if row["checkedOn"] != document.get("checkedOn"):
            problems.append(f"{where}: inconsistent check date")
        for key in ("title", "section", "scenario", "gap"):
            if not isinstance(row[key], str) or len(row[key].strip()) < (2 if key in ("title", "section") else 6):
                problems.append(f"{where}: empty or short {key}")
        if isinstance(row["section"], str):
            sections.add(row["section"])
    if len(sections) < 8:
        problems.append("coverage must span several independent help sections")
    return problems


def main() -> int:
    document = json.loads(FILE.read_text(encoding="utf-8"))
    issues = validate(document, MATRIX.read_text(encoding="utf-8"))
    if issues:
        print("FAIL: AutoDealer page-by-page inventory contract:")
        for issue in issues:
            print(f" - {issue}")
        return 1
    print(f"PASS: {len(document['records'])} source-linked Online pages,"
          " matrix IDs, owner issues and explicit PARTIAL status")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
