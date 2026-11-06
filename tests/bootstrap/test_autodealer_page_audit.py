"""Offline regression contract for the AutoDealer help → owned GAP issue inventory."""
import copy
import json

from scripts.check_autodealer_audit import FILE, MATRIX, validate


def _data():
    return json.loads(FILE.read_text(encoding="utf-8"))


def _matrix():
    return MATRIX.read_text(encoding="utf-8")


def test_public_online_inventory_has_real_owner_and_no_claimed_completion():
    data=_data()
    assert not validate(data,_matrix())
    assert len(data["records"])>=25
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
