"""Регрессия независимого валидатора OpenAPI 3.1 (мутационные тесты)."""
from copy import deepcopy
from pathlib import Path

import pytest
import yaml
from openapi_spec_validator import validate_spec

SPEC = yaml.safe_load((Path(__file__).resolve().parents[2] / "contracts/openapi.yaml").read_text(encoding="utf-8"))


def test_project_contract_passes_independent_validation():
    validate_spec(SPEC)


def test_unresolvable_reference_fails_independent_validation():
    changed = deepcopy(SPEC)
    changed["paths"]["/api/v1/catalog/brands"]["get"]["responses"]["200"] = {
        "description": "Broken reference",
        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/DefinitelyMissing"}}},
    }
    with pytest.raises(Exception):
        validate_spec(changed)


def test_invalid_response_code_fails_independent_validation():
    changed = deepcopy(SPEC)
    changed["paths"]["/api/v1/catalog/brands"]["get"]["responses"]["invalid-http-status"] = {
        "description": "Bad status code"
    }
    with pytest.raises(Exception):
        validate_spec(changed)


def test_operation_ids_unique():
    ids = [
        operation["operationId"]
        for path in SPEC["paths"].values()
        for method, operation in path.items()
        if method in {"get", "post", "put", "delete", "patch"}
    ]
    assert len(ids) == len(set(ids))
