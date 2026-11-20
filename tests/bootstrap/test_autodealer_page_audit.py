"""Offline regression contract for the AutoDealer help → owned GAP issue inventory."""
import copy
import json

from scripts.check_autodealer_audit import FILE, MATRIX, REQUIREMENTS, validate


def _data():
    return json.loads(FILE.read_text(encoding="utf-8"))


def _matrix():
    return MATRIX.read_text(encoding="utf-8")


def test_public_online_inventory_has_real_owner_and_no_claimed_completion():
    data=_data()
    assert not validate(data,_matrix())
    assert len(data["records"])>=77
    assert all(item["url"].startswith("https://autodealer.ru/online/help/")
               for item in data["records"])


def test_inventory_fails_closed_on_wrong_source_duplicate_or_unowned_gap():
    original=_data()
    for broken in [
        {"url":"https://evil.example/online/help/fake.html"},
        {"matrixId":"Z99"},
        {"issue":None},
        {"status":"PRODUCTION"},
        {"evidence":"guessed"},
        {"checkedOn":"2025-01-01"},
        {"scenario":""},
    ]:
        data=copy.deepcopy(original)
        data["records"][0].update(broken)
        assert validate(data,_matrix()),broken
    data=copy.deepcopy(original)
    data["records"][1]["url"]=data["records"][0]["url"]
    assert any("duplicate URL" in msg for msg in validate(data,_matrix()))


def test_inventory_does_not_silently_claim_complete_tree():
    data=_data()
    data["completeness"]="COMPLETE - all online help pages audited"
    assert any("incomplete" in msg for msg in validate(data,_matrix()))



def test_november_20_sources_have_atomic_owned_acceptance_criteria():
    inventory = _data()
    requirement_doc = json.loads(REQUIREMENTS.read_text(encoding="utf-8"))
    pages = {row["id"]: row for row in inventory["records"]}
    requirements_by_page = {}
    for row in requirement_doc["requirements"]:
        requirements_by_page.setdefault(row["pageId"], []).append(row)
    expected = {
        "ADP-055": ("C02", 115, "page"),
        "ADP-056": ("D07", 170, "search_excerpt"),
        "ADP-057": ("D12", 169, "page"),
        "ADP-058": ("B03", 167, "page"),
        "ADP-059": ("D07", 170, "page"),
        "ADP-060": ("C02", 115, "page"),
        "ADP-061": ("C02", 115, "search_excerpt"),
        "ADP-062": ("C04", 168, "search_excerpt"),
        "ADP-063": ("E10", 107, "search_excerpt"),
        "ADP-064": ("C04", 168, "page"),
        "ADP-065": ("D09", 171, "page"),
        "ADP-066": ("A05", 446, "search_excerpt"),
        "ADP-069": ("A05", 446, "page"),
        "ADP-070": ("C08", 168, "page"),
        "ADP-071": ("C06", 168, "page"),
        "ADP-072": ("C04", 168, "page"),
        "ADP-073": ("C07", 168, "search_excerpt"),
    }
    nav_link_ids = {"ADP-067", "ADP-068", "ADP-074", "ADP-075", "ADP-076", "ADP-077"}
    assert len(inventory["records"]) >= 77
    assert len(requirement_doc["requirements"]) >= 127
    for page_id, (matrix_id, owner_issue, evidence) in expected.items():
        page = pages[page_id]
        criteria = requirements_by_page[page_id]
        assert (page["matrixId"], page["issue"], page["evidence"]) == (
            matrix_id, owner_issue, evidence
        )
        assert len(criteria) == (2 if page_id == "ADP-069" else 1)
        for criterion in criteria:
            assert criterion["matrixId"] == matrix_id
            assert criterion["ownerIssue"] == owner_issue
            assert criterion["sourceEvidence"] == evidence
            assert criterion["status"] == "NOT_PROVEN_COMPLETE"
            assert criterion["evidencePaths"] == []
    for page_id in nav_link_ids:
        assert pages[page_id]["evidence"] == "nav_link"
        assert pages[page_id]["status"] == "PLANNED"
        assert page_id not in requirements_by_page


def test_added_requirements_cover_inventory_and_staff_boundaries():
    requirement_doc = json.loads(REQUIREMENTS.read_text(encoding="utf-8"))
    by_id = {row["id"]: row for row in requirement_doc["requirements"]}
    expected = {
        "ADF-104": "ADP-005",
        "ADF-105": "ADP-005",
        "ADF-106": "ADP-005",
        "ADF-107": "ADP-032",
        "ADF-108": "ADP-032",
        "ADF-109": "ADP-032",
        "ADF-110": "ADP-032",
        "ADF-111": "ADP-032",
        "ADF-112": "ADP-034",
        "ADF-113": "ADP-034",
        "ADF-114": "ADP-035",
        "ADF-115": "ADP-048",
        "ADF-116": "ADP-048",
    }
    for requirement_id, page_id in expected.items():
        row = by_id[requirement_id]
        assert row["pageId"] == page_id
        assert row["status"] == "NOT_PROVEN_COMPLETE"
        assert row["evidencePaths"] == []
