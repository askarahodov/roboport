"""Проверка реальных HTTP-ответов по JSON Schema из проектного OpenAPI 3.1.

Проверяем только реализованные endpoints, не выдаём остальные операции за готовые.
"""
from pathlib import Path
from uuid import UUID

import jsonschema
import pytest
import yaml
from fastapi.testclient import TestClient

from roboport_api.main import app

SPEC = yaml.safe_load((Path(__file__).resolve().parents[2] / "contracts/openapi.yaml").read_text(encoding="utf-8"))
CLIENT = TestClient(app)


def validate_response(path, status, payload):
    entry = SPEC["paths"][path]["get"]["responses"][str(status)]
    schema = entry["content"]["application/json"]["schema"]
    # Локальные $ref разрешаются относительно корня исходного OAS документа.
    resolver = jsonschema.RefResolver.from_schema(SPEC)
    jsonschema.Draft202012Validator(schema, resolver=resolver, format_checker=jsonschema.FormatChecker()).validate(payload)


@pytest.mark.parametrize("path", ["/api/v1/catalog/brands", "/api/v1/catalog/models"])
def test_catalog_200_matches_openapi_schema(path):
    response = CLIENT.get(path)
    assert response.status_code == 200
    validate_response(path, 200, response.json())


def test_unknown_model_404_matches_error_schema():
    response = CLIENT.get("/api/v1/catalog/models/unknown")
    assert response.status_code == 404
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-request-id"] == response.json()["requestId"]
    UUID(response.json()["requestId"])
    validate_response("/api/v1/catalog/models/{modelId}", 404, response.json())


def test_client_supplied_request_id_is_never_trusted():
    response = CLIENT.get("/api/v1/catalog/models/unknown", headers={"X-Request-ID": "attacker-id"})
    assert response.status_code == 404
    assert response.headers["x-request-id"] != "attacker-id"


def test_mutated_catalog_payload_fails_json_schema():
    with pytest.raises(jsonschema.ValidationError):
        validate_response("/api/v1/catalog/brands", 200, [])
    with pytest.raises(jsonschema.ValidationError):
        validate_response("/api/v1/catalog/models/{modelId}", 404, {"detail": "Model not found"})
