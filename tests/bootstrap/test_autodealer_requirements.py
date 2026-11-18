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


def test_49_public_pages_and_all_89_acceptance_criteria_are_traced():
    pages = _pages()
    checks = _requirements()
    assert len(pages["records"]) == 49
    assert len(checks["requirements"]) == 89
    assert len({r["pageId"] for r in checks["requirements"]}) == 49
    assert not validate(pages, MATRIX.read_text(encoding="utf-8"))
    assert not validate_requirements(checks, pages)
    assert "PARTIAL" in pages["completeness"]
    assert all(x["status"] == "NOT_PROVEN_COMPLETE" for x in checks["requirements"])
    # Never convert a source article or a named screen to completion evidence.
    assert all(not x["evidencePaths"] for x in checks["requirements"])


def test_old_source_dates_are_preserved_and_future_or_stale_dates_fail_closed():
    pages = _pages()
    old = [x for x in pages["records"] if x["checkedOn"] == "2026-11-06"]
    new = [x for x in pages["records"] if x["checkedOn"] == "2026-11-18"]
    assert len(old) == 30 and len(new) == 19
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
