"""Проверка проектной карты FSM без заявления о готовности runtime guards."""
import json
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[2]
GRAPH = json.loads((ROOT / "docs/domain/TICKET_TRANSITIONS.json").read_text(encoding="utf-8"))
SPEC = yaml.safe_load((ROOT / "contracts/openapi.yaml").read_text(encoding="utf-8"))
STATES = set(SPEC["components"]["schemas"]["Ticket"]["properties"]["state"]["enum"])


def test_fsm_graph_covers_all_exact_openapi_states():
    assert set(GRAPH["transitions"]) == STATES
    for start, targets in GRAPH["transitions"].items():
        assert len(set(targets)) == len(targets), start
        assert all(target in STATES for target in targets), start


def test_safety_hold_cannot_bypass_expert_release():
    assert GRAPH["transitions"]["SAFETY_HOLD"] == []
    assert "SAFETY_HOLD" in GRAPH["transitions"]["DIAGNOSING"]


def test_qc_failed_blocks_handover():
    assert "READY_FOR_HANDOVER" not in GRAPH["transitions"]["QC_FAILED"]
    assert "COMPLETED" not in GRAPH["transitions"]["QC_FAILED"]
    assert GRAPH["transitions"]["QC_FAILED"] == ["REPAIRING"]


def test_terminal_and_special_states_fail_closed():
    for state in ("COMPLETED", "CANCELLED", "UNREPAIRABLE", "WARRANTY_ROUTE", "DECLINED"):
        assert GRAPH["transitions"][state] == []


def test_no_implicit_skip_of_estimate_or_qc():
    assert GRAPH["transitions"]["ESTIMATE_DRAFT"] == ["AWAITING_APPROVAL"]
    assert "REPAIRING" not in GRAPH["transitions"]["AWAITING_APPROVAL"]
    assert "COMPLETED" not in GRAPH["transitions"]["REPAIRING"]
    assert GRAPH["status"] == "draft-contract-only"
