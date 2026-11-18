#!/usr/bin/env python3
"""Offline structural contract for page-by-page AutoDealer Online gap inventory.

Per-row source dates survive incremental discovery. Evidence older than 60d
must be reviewed rather than falsely stamped with the latest inventory date.
Never crawls an external website in CI. Checking source contents, freshness and
business acceptance requires an independent human/web review (issue #108).
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
import re
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
FILE = ROOT / "docs" / "auto" / "AUTODEALER_ONLINE_PAGES.json"
MATRIX = ROOT / "docs" / "auto" / "FUNCTIONAL_MATRIX.md"
REQUIREMENTS = ROOT / "docs" / "auto" / "AUTODEALER_FUNCTION_REQUIREMENTS.json"
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
    try:
        reviewed = date.fromisoformat(document["checkedOn"])
        if not 2025 <= reviewed.year <= 2100:
            raise ValueError("unsupported audit year")
    except (KeyError, TypeError, ValueError):
        reviewed = None
        problems.append("missing or invalid checkedOn")
    records = document.get("records")
    if not isinstance(records, list):
        return problems + ["records must be an array"]
    if len(records) < 49:
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
        try:
            evidence_day = date.fromisoformat(row["checkedOn"])
            if reviewed is None or not reviewed - timedelta(days=60) <= evidence_day <= reviewed:
                problems.append(f"{where}: inconsistent or stale check date")
        except (TypeError, ValueError):
            problems.append(f"{where}: invalid source check date")
        for key in ("title", "section", "scenario", "gap"):
            if not isinstance(row[key], str) or len(row[key].strip()) < (2 if key in ("title", "section") else 6):
                problems.append(f"{where}: empty or short {key}")
        if isinstance(row["section"], str):
            sections.add(row["section"])
    if len(sections) < 8:
        problems.append("coverage must span several independent help sections")
    return problems



def validate_requirements(document: object, inventory: object) -> list[str]:
    """Ensure every evidenced source page has executable acceptance criteria.

    The validator is strictly offline: a green structural check must never be
    represented as an independent feature, E2E, legal or production sign-off.
    """
    if not isinstance(document, dict) or not isinstance(inventory, dict):
        return ["requirements or source inventory is not an object"]
    problems = []
    if set(document) != {"schemaVersion","checkedOn","source",
                         "completeness","requirements"}:
        problems.append("unexpected requirement manifest fields")
    if document.get("schemaVersion") != 1:
        problems.append("unsupported requirements schemaVersion")
    if document.get("source") != "https://autodealer.ru/online/help/":
        problems.append("wrong Online help source root")
    if "PARTIAL" not in str(document.get("completeness","")):
        problems.append("unverified completeness must remain PARTIAL")
    if document.get("checkedOn") != inventory.get("checkedOn"):
        problems.append("requirements and page inventory dates diverge")
    pages = {x["id"]:x for x in inventory.get("records",[])
             if isinstance(x,dict) and isinstance(x.get("id"),str)}
    items = document.get("requirements")
    if not isinstance(items,list):
        return problems+["requirements must be a list"]
    if len(items)<80:
        problems.append("atomic acceptance requirements silently shrank")
    seen_ids=set()
    covered=set()
    duplicates=set()
    required={"id","pageId","matrixId","ownerIssue","capability","acceptance",
              "status","evidencePaths","sourceEvidence"}
    for index,item in enumerate(items,1):
        where=f"requirements[{index}]"
        if not isinstance(item,dict) or set(item)!=required:
            problems.append(f"{where}: invalid fields")
            continue
        ident=item["id"]
        if not isinstance(ident,str) or re.fullmatch(r"ADF-\\d{3}",ident) is None:
            problems.append(f"{where}: invalid ID")
        elif ident in seen_ids:
            problems.append(f"{where}: duplicate ID")
        seen_ids.add(ident)
        page=pages.get(item["pageId"])
        if page is None:
            problems.append(f"{where}: unknown source page")
        else:
            covered.add(item["pageId"])
            for attr,expected in (("matrixId",page["matrixId"]),
                                  ("ownerIssue",page["issue"]),
                                  ("sourceEvidence",page["evidence"])):
                if item[attr]!=expected:
                    problems.append(f"{where}: source mismatch {attr}")
        capability=item["capability"]
        acceptance=item["acceptance"]
        if (not isinstance(capability,str) or len(capability.strip())<8
            or not isinstance(acceptance,str) or len(acceptance.strip())<45):
            problems.append(f"{where}: vague capability or acceptance")
        if isinstance(capability,str):
            key=(item["pageId"],capability.casefold().strip())
            if key in duplicates:
                problems.append(f"{where}: duplicate capability on source page")
            duplicates.add(key)
        status=item["status"]
        evidence=item["evidencePaths"]
        if status not in {"NOT_PROVEN_COMPLETE","DEV_VERIFIED"}:
            problems.append(f"{where}: cannot assert production completion")
        if not isinstance(evidence,list) or not all(
                isinstance(p,str) for p in evidence):
            problems.append(f"{where}: invalid implementation evidence")
            continue
        if status=="NOT_PROVEN_COMPLETE" and evidence:
            problems.append(f"{where}: unverified capability carries false evidence")
        if status=="DEV_VERIFIED":
            # Paths are necessary but NOT sufficient evidence of correctness:
            # CI result + human QA + security gates must be reviewed outside CI.
            if not any(p.startswith("tests/") for p in evidence) or not any(
                    p.startswith("docs/") for p in evidence):
                problems.append(f"{where}: tested+documented evidence required")
            if any(p.startswith("/") or ".." in p.split("/")
                   or not (ROOT/p).is_file() for p in evidence):
                problems.append(f"{where}: invalid evidence path")
    missing=set(pages)-covered
    if missing:
        problems.append(f"{len(missing)} source pages lack acceptance requirements")
    return problems


def main() -> int:
    document = json.loads(FILE.read_text(encoding="utf-8"))
    issues = validate(document, MATRIX.read_text(encoding="utf-8"))
    requirements = json.loads(REQUIREMENTS.read_text(encoding="utf-8"))
    issues.extend(validate_requirements(requirements, document))
    if issues:
        print("FAIL: AutoDealer page-by-page inventory contract:")
        for issue in issues:
            print(f" - {issue}")
        return 1
    print(f"PASS: {len(document['records'])} source-linked Online pages, "
          f"{len(requirements['requirements'])} testable acceptance requirements;"
          " source coverage is PARTIAL and implementation is NOT proven")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
