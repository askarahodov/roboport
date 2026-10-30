"""Fail-closed checks for the documented automotive release evidence gate."""
from scripts.check_auto_release_evidence import REQUIRED_GATES, validate_evidence, main


def complete():
    return {
        "gates": {key: {"approved": True, "evidence_url": f"https://github.com/example/repo/{key}"}
                  for key in REQUIRED_GATES},
        "approved_by": "independent-reviewer",
        "approved_at": "2026-10-30T10:00:00Z",
    }


def test_missing_evidence_and_empty_gates_block(tmp_path):
    assert validate_evidence(None)
    assert validate_evidence({})
    assert main(["check_auto_release_evidence.py"]) == 2
    assert main(["check_auto_release_evidence.py", str(tmp_path / "missing.json")]) == 2


def test_partial_and_unapproved_gates_block():
    for key in REQUIRED_GATES:
        item = complete()
        del item["gates"][key]
        assert validate_evidence(item) == ("MISSING_OR_EXTRA_GATES",)
        item = complete()
        item["gates"][key]["approved"] = False
        assert key in validate_evidence(item)
        item = complete()
        item["gates"][key]["evidence_url"] = ""
        assert key in validate_evidence(item)


def test_invalid_signoff_blocks():
    for field, value in [("approved_by", ""), ("approved_by", None),
                         ("approved_at", ""), ("approved_at", None)]:
        item = complete()
        item[field] = value
        assert validate_evidence(item)


def test_structurally_complete_document_only_passes_schema():
    assert validate_evidence(complete()) == ()
