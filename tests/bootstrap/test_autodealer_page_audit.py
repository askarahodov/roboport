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
        "ADP-058": ("B03", 114, "page"),
        "ADP-059": ("D07", 170, "page"),
        "ADP-060": ("C02", 115, "page"),
        "ADP-061": ("C02", 115, "search_excerpt"),
        "ADP-062": ("C04", 168, "search_excerpt"),
        "ADP-063": ("E10", 107, "search_excerpt"),
        "ADP-064": ("C04", 168, "page"),
        "ADP-065": ("D09", 171, "page"),
        "ADP-066": ("A05", 107, "search_excerpt"),
        "ADP-069": ("A05", 119, "page"),
        "ADP-070": ("C08", 168, "page"),
        "ADP-071": ("C06", 168, "page"),
        "ADP-072": ("C04", 168, "page"),
        "ADP-073": ("C07", 168, "search_excerpt"),
        "ADP-078": ("B10", 441, "page"),
        "ADP-081": ("B01", 441, "search_excerpt"),
        "ADP-082": ("B02", 439, "search_excerpt"),
        "ADP-083": ("A05", 448, "page"),
    }
    nav_link_ids = {"ADP-067", "ADP-068", "ADP-074", "ADP-075", "ADP-076", "ADP-077", "ADP-085", "ADP-086", "ADP-087", "ADP-088"}
    assert len(inventory["records"]) >= 85
    assert len(requirement_doc["requirements"]) >= 150
    for page_id, (matrix_id, owner_issue, evidence) in expected.items():
        page = pages[page_id]
        criteria = requirements_by_page[page_id]
        assert (page["matrixId"], page["issue"], page["evidence"]) == (
            matrix_id, owner_issue, evidence
        )
        assert len(criteria) == (3 if page_id == "ADP-057" else 2 if page_id in {"ADP-069", "ADP-078"} else 1)
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
        "ADF-128": "ADP-002",
        "ADF-129": "ADP-002",
        "ADF-130": "ADP-003",
        "ADF-131": "ADP-004",
        "ADF-132": "ADP-006",
        "ADF-133": "ADP-007",
        "ADF-134": "ADP-007",
        "ADF-135": "ADP-016",
        "ADF-136": "ADP-016",
        "ADF-137": "ADP-031",
        "ADF-138": "ADP-031",
        "ADF-139": "ADP-032",
        "ADF-140": "ADP-057",
        "ADF-141": "ADP-057",
        "ADF-142": "ADP-078",
        "ADF-143": "ADP-078",
        "ADF-144": "ADP-050",
        "ADF-145": "ADP-012",
        "ADF-146": "ADP-012",
        "ADF-147": "ADP-081",
        "ADF-148": "ADP-082",
        "ADF-149": "ADP-083",
        "ADF-150": "ADP-042",
    }
    for requirement_id, page_id in expected.items():
        row = by_id[requirement_id]
        assert row["pageId"] == page_id
        assert row["status"] == "NOT_PROVEN_COMPLETE"
        assert row["evidencePaths"] == []



def test_residual_ownership_after_technical_closure():
    # Исторические технические задачи закрыты только в synthetic-границе.
    # Остаток на 27.11.2026 привязан к существующим открытым задачам.
    # Offline fixture фиксирует решение, но не запрашивает live GitHub.
    expected = {
        "ADP-004": 114,
        "ADP-008": 107,
        "ADP-012": 114,
        "ADP-016": 119,
        "ADP-034": 107,
        "ADP-035": 119,
        "ADP-058": 114,
        "ADP-066": 107,
        "ADP-067": 107,
        "ADP-069": 119,
        "ADP-083": 448,
        "ADP-086": 114,
    }
    pages = {page["id"]: page for page in _data()["records"]}
    requirements = json.loads(REQUIREMENTS.read_text(encoding="utf-8"))["requirements"]
    assert all(page["issue"] not in {113, 167, 446} for page in pages.values())
    for page_id, owner_issue in expected.items():
        assert pages[page_id]["issue"] == owner_issue
        rows = [row for row in requirements if row["pageId"] == page_id]
        assert bool(rows) == (pages[page_id]["evidence"] != "nav_link")
        for row in rows:
            assert row["ownerIssue"] == owner_issue
            assert row["status"] == "NOT_PROVEN_COMPLETE"
            assert row["evidencePaths"] == []
