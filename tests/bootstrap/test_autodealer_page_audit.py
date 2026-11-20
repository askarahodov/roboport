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
    assert len(data["records"])>=61
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
    requirements_by_page = {row["pageId"]: row for row in requirement_doc["requirements"]}
    expected = {
        "ADP-055": ("C02", 115, "page"),
        "ADP-056": ("D07", 170, "search_excerpt"),
        "ADP-057": ("D12", 169, "page"),
        "ADP-058": ("B03", 167, "page"),
        "ADP-059": ("D07", 170, "page"),
        "ADP-060": ("C02", 115, "page"),
        "ADP-061": ("C02", 115, "search_excerpt"),
    }
    assert len(inventory["records"]) >= 61
    assert len(requirement_doc["requirements"]) >= 103
    for page_id, (matrix_id, owner_issue, evidence) in expected.items():
        page = pages[page_id]
        criterion = requirements_by_page[page_id]
        assert (page["matrixId"], page["issue"], page["evidence"]) == (
            matrix_id, owner_issue, evidence
        )
        assert criterion["matrixId"] == matrix_id
        assert criterion["ownerIssue"] == owner_issue
        assert criterion["sourceEvidence"] == evidence
        assert criterion["status"] == "NOT_PROVEN_COMPLETE"
        assert criterion["evidencePaths"] == []
