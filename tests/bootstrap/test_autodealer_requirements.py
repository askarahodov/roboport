"""Source-evidence and functional acceptance traceability for Online parity #108."""
import copy
import json

from scripts.check_autodealer_audit import (
    FILE, REQUIREMENTS, MATRIX, validate, validate_requirements,
)


def _pages():
    return json.loads(FILE.read_text(encoding="utf-8"))


def _requirements():
    return json.loads(REQUIREMENTS.read_text(encoding="utf-8"))


def test_all_public_pages_and_acceptance_criteria_are_traced():
    pages = _pages()
    checks = _requirements()
    # Discovery is additive: fail if baseline coverage is lost, not when new
    # independently sourced pages and acceptance criteria are recorded.
    assert len(pages["records"]) >= 85
    assert len(checks["requirements"]) >= 150
    assert len({r["id"] for r in pages["records"]}) == len(pages["records"])
    evidenced_pages = {r["id"] for r in pages["records"] if r["evidence"] != "nav_link"}
    requirement_pages = {r["pageId"] for r in checks["requirements"]}
    assert requirement_pages == evidenced_pages
    assert all(r["id"] not in requirement_pages for r in pages["records"] if r["evidence"] == "nav_link")
    assert not validate(pages, MATRIX.read_text(encoding="utf-8"))
    assert not validate_requirements(checks, pages)
    assert "PARTIAL" in pages["completeness"]
    assert all(x["status"] == "NOT_PROVEN_COMPLETE" for x in checks["requirements"])
    # Never convert a source article or a named screen to completion evidence.
    assert all(not x["evidencePaths"] for x in checks["requirements"])


def test_old_source_dates_are_preserved_and_future_or_stale_dates_fail_closed():
    pages = _pages()
    rows = {x["id"]: x for x in pages["records"]}
    # A source inspection changes the date only for that source, so count-based
    # assertions would punish legitimate re-verification of older articles.
    assert rows["ADP-001"]["checkedOn"] == "2026-11-06"
    assert rows["ADP-049"]["checkedOn"] == "2026-11-18"
    assert rows["ADP-020"]["checkedOn"] == "2026-11-19"
    assert rows["ADP-050"]["checkedOn"] == "2026-11-20"
    assert len({x["checkedOn"] for x in rows.values()}) >= 3
    for value in ("2025-01-01", "2026-12-01", "invalid"):
        altered = copy.deepcopy(pages)
        altered["records"][0]["checkedOn"] = value
        assert validate(altered, MATRIX.read_text(encoding="utf-8")), value


def test_capability_claims_require_source_and_real_test_and_doc_evidence():
    pages = _pages()
    original = _requirements()
    for change in [
        {"pageId": "ADP-999"},
        {"matrixId": "Z99"},
        {"sourceEvidence": "invented"},
        {"ownerIssue": 999999},
        {"status": "PRODUCTION_VERIFIED"},
        {"status": "DEV_VERIFIED"},
        {"acceptance": ""},
        {"id": "ADF-x"},
        {"evidencePaths": ["tests/fake.py"]},
    ]:
        altered = copy.deepcopy(original)
        altered["requirements"][0].update(change)
        assert validate_requirements(altered, pages), change
    altered = copy.deepcopy(original)
    altered["requirements"][1]["id"] = altered["requirements"][0]["id"]
    assert any("duplicate ID" in x for x in validate_requirements(altered, pages))
    altered = copy.deepcopy(original)
    altered["requirements"] = [x for x in altered["requirements"]
                                if x["pageId"] != "ADP-049"]
    assert any("lack acceptance" in x for x in validate_requirements(altered, pages))


def test_requirements_must_not_claim_exhaustive_help_navigation():
    checks = _requirements()
    checks["completeness"] = "COMPLETE_ALL_FEATURES"
    assert validate_requirements(checks, _pages())
