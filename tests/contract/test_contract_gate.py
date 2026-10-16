"""Mutation proof for the real independent OpenAPI contract gate."""
import json
import sys
from copy import deepcopy
from pathlib import Path

import pytest
import yaml

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/"scripts"))
from check_contract import check_contract

SPEC=yaml.safe_load((ROOT/"contracts/openapi.yaml").read_text(encoding="utf-8"))
MANIFEST=json.loads((ROOT/"contracts/operation_ids.json").read_text(encoding="utf-8"))


def test_independent_gate_on_actual_versioned_contract():
    assert check_contract(SPEC,MANIFEST)==46


def test_unknown_operation_id_rejected():
    broken=deepcopy(SPEC)
    broken["paths"]["/api/v1/catalog/brands"]["get"]["operationId"]="unexpectedUndocumentedOperation"
    with pytest.raises(AssertionError,match="operationId"):
        check_contract(broken,MANIFEST)


def test_missing_success_status_rejected():
    broken=deepcopy(SPEC)
    broken["paths"]["/api/v1/catalog/brands"]["get"]["responses"].pop("200")
    with pytest.raises(AssertionError,match="success status"):
        check_contract(broken,MANIFEST)


def test_noncanonical_error_rejected():
    broken=deepcopy(SPEC)
    broken["paths"]["/api/v1/catalog/models/{modelId}"]["get"]["responses"]["404"]["content"]["application/json"]["schema"]={"type":"object"}
    with pytest.raises(AssertionError,match="canonical Error"):
        check_contract(broken,MANIFEST)


def test_unresolved_schema_reference_rejected():
    broken=deepcopy(SPEC)
    broken["paths"]["/api/v1/catalog/brands"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]={"$ref":"#/components/schemas/NotAThing"}
    with pytest.raises(Exception):
        check_contract(broken,MANIFEST)


def test_cookie_scheme_change_rejected():
    broken=deepcopy(SPEC)
    broken["components"]["securitySchemes"]["localSession"]["name"]="wrong_session"
    with pytest.raises(AssertionError,match="Opaque cookie"):
        check_contract(broken,MANIFEST)


def test_new_head_operation_rejected_even_if_oas_valid():
    broken=deepcopy(SPEC)
    broken["paths"]["/api/v1/catalog/brands"]["head"]={
        "operationId":"undocumentedHead","responses":{"204":{"description":"Empty"}}
    }
    with pytest.raises(AssertionError,match="operationId"):
        check_contract(broken,MANIFEST)


def test_error_required_fields_change_rejected():
    broken=deepcopy(SPEC)
    broken["components"]["schemas"]["Error"]["required"].remove("requestId")
    with pytest.raises(AssertionError,match="Canonical Error"):
        check_contract(broken,MANIFEST)


def test_noncanonical_429_response_rejected():
    broken=deepcopy(SPEC)
    entry=broken["paths"]["/api/v1/auth/login"]["post"]["responses"]["429"]
    entry["content"]["application/json"]["schema"]={"type":"object"}
    with pytest.raises(AssertionError,match="canonical Error"):
        check_contract(broken,MANIFEST)
