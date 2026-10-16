"""Проверка канонической SQL/API/ACL матрицы и схемы FSM (#17)."""
import json
import re
from copy import deepcopy
from pathlib import Path

import pytest
import yaml

ROOT=Path(__file__).resolve().parents[2]
MATRIX=json.loads((ROOT/"docs/data/SCHEMA_FIELD_MATRIX.json").read_text(encoding="utf-8"))
SPEC=yaml.safe_load((ROOT/"contracts/openapi.yaml").read_text(encoding="utf-8"))
FSM=json.loads((ROOT/"docs/domain/TICKET_TRANSITIONS.json").read_text(encoding="utf-8"))


def validate_matrix(matrix=MATRIX,spec=SPEC):
    assert matrix["contract_version"]==spec["info"]["version"], "Contract version mismatch"
    assert matrix["adr"]=="ADR-032"
    names=[entry["field"] for entry in matrix["fields"]]
    assert len(names)==len(set(names)), "Duplicate SQL field"
    assert len(names)>=20
    for row in matrix["fields"]:
        assert row["status"] in ("implemented","planned")
        assert row["guard"].strip(), "Missing security decision"
        assert row["sql_type"].strip(), "Missing SQL type"
        assert row["constraint"].strip(), "Missing SQL constraint or planned decision"
        assert row["owner_module"].strip(), "Missing module owner"
        assert (ROOT/row["test_path"]).is_file(), "Missing actual regression test path"
        if row["status"]=="planned":
            assert row["constraint"].startswith("PLANNED:"), "Planned field masquerades as implemented"
        if row["status"]=="planned":
            assert row["migration"]=="none", "Planned field masquerades as migrated"
        else:
            migration=ROOT/"apps/api/alembic/versions"/(row["migration"]+".py")
            assert migration.is_file(), row["field"]
            code=migration.read_text(encoding="utf-8")
            table,column=row["field"].split(".",1)
            assert re.search(r"CREATE TABLE\s+"+re.escape(table)+r"\s*\(",code), (
                f"{row['field']}: missing SQL table declaration")
            assert re.search(r"\b"+re.escape(column)+r"\b",code), (
                f"{row['field']}: missing migration column")
        if row["api"] is not None:
            schema,property_name=row["api"].split(".",1)
            assert property_name in spec["components"]["schemas"][schema]["properties"], (
                f"Missing OAS field: {row['api']}")
    return len(names)


def check_fsm(spec=SPEC,fsm=FSM):
    states=set(spec["components"]["schemas"]["Ticket"]["properties"]["state"]["enum"])
    targets=set(spec["components"]["schemas"]["Transition"]["properties"]["targetState"]["enum"])
    assert len(states)==20
    assert targets==states-{"DRAFT"}, "Obsolete or missing Transition states"
    assert set(fsm["transitions"])==states, "FSM missing source states"
    assert all(set(destinations)<=states for destinations in fsm["transitions"].values())
    for terminal in ("SAFETY_HOLD","COMPLETED","CANCELLED","UNREPAIRABLE","WARRANTY_ROUTE"):
        assert fsm["transitions"][terminal]==[], f"Unsafe terminal transition: {terminal}"
    assert "READY_FOR_HANDOVER" not in fsm["transitions"]["QC_FAILED"]
    return states


def test_field_sql_api_security_matrix_matches_real_migrations():
    assert validate_matrix()==len(MATRIX["fields"])


def test_ticket_state_and_target_are_canonical():
    assert len(check_fsm())==20


def test_obsolete_transition_alias_breaks_gate():
    changed=deepcopy(SPEC)
    changed["components"]["schemas"]["Transition"]["properties"]["targetState"]["enum"].append("IN_REPAIR")
    with pytest.raises(AssertionError,match="Obsolete"):
        check_fsm(changed,FSM)


def test_invalid_mapping_to_nonexistent_api_property_breaks_gate():
    changed=deepcopy(MATRIX)
    changed["fields"][0]["api"]="Ticket.unknownOldField"
    with pytest.raises(AssertionError,match="Missing OAS field"):
        validate_matrix(changed,SPEC)


def test_missing_sql_field_breaks_gate():
    changed=deepcopy(MATRIX)
    changed["fields"][0]["field"]="service_ticket.legacy_nonexistent_column"
    with pytest.raises(AssertionError,match="missing migration column"):
        validate_matrix(changed,SPEC)


def test_schema_adr_decisions_and_legacy_drafts_are_explicit():
    adr=(ROOT/"docs/architecture/ADR.md").read_text(encoding="utf-8")
    assert "ADR-032" in adr and "service_ticket.customer_id" in adr
    assert "SUPERSEDED" in adr and "planned" in adr
    legacy=ROOT/"db/legacy_drafts"
    assert len(list(legacy.glob("*.sql")))==6
    assert all(p.stat().st_size==0 for p in legacy.glob("*.sql"))


def test_missing_constraint_metadata_breaks_mapping_gate():
    broken=deepcopy(MATRIX)
    broken["fields"][0].pop("constraint")
    with pytest.raises(KeyError):
        validate_matrix(broken,SPEC)


def test_ticket_customer_acl_separates_staff_create_from_client_read():
    row=next(x for x in MATRIX["fields"] if x["field"]=="service_ticket.customer_id")
    assert all(token in row["guard"] for token in ("INTAKE","MANAGER","SCOPE_CLIENT","CLIENT","account_customer_link"))
