"""Independent OAS 3.1 + project contract gate; fails closed on unreviewed drift."""
import json
from pathlib import Path

import yaml
from openapi_spec_validator import validate_spec

ROOT = Path(__file__).resolve().parents[1]
SPEC = yaml.safe_load((ROOT / "contracts/openapi.yaml").read_text(encoding="utf-8"))
MANIFEST = json.loads((ROOT / "contracts/operation_ids.json").read_text(encoding="utf-8"))
METHODS = {"get", "post", "put", "patch", "delete", "head", "options", "trace"}
ERRORS = {"400", "401", "403", "404", "409", "422", "429"}


def check_contract(spec=SPEC, manifest=MANIFEST):
    validate_spec(spec)
    assert spec["openapi"].startswith("3.1."), "OpenAPI 3.1 required"
    assert spec["info"]["version"] == manifest["contract_version"], "Contract version drift"
    ops = [(p, verb, op) for p, methods in spec["paths"].items()
           for verb, op in methods.items() if verb in METHODS]
    ids = [op["operationId"] for _, _, op in ops]
    assert len(ids) == len(set(ids)), "Duplicate operationId"
    assert sorted(ids) == sorted(manifest["operation_ids"]), "Unknown/missing operationId: ADR required"
    assert len(ids) == 46, "Reviewed operation count changed"
    for path, verb, operation in ops:
        responses = operation["responses"]
        assert any(code.isdigit() and 200 <= int(code) < 300 for code in responses), (
            f"{verb} {path}: missing success status")
        for code, response in responses.items():
            if code not in ERRORS:
                continue
            content = response.get("content", {}).get("application/json", {})
            schema = content.get("schema")
            assert schema == {"$ref": "#/components/schemas/Error"}, (
                f"{verb} {path}: {code} must use canonical Error")
    error=spec["components"]["schemas"]["Error"]
    assert error["type"]=="object", "Error must be an object"
    assert set(error["required"])=={"code","message","requestId"}, "Canonical Error fields changed"
    assert error.get("additionalProperties") is False, "Canonical Error must reject unknown fields"
    properties=error["properties"]
    assert properties["code"]["type"]=="string" and properties["message"]["type"]=="string"
    assert properties["requestId"]=={"type":"string","format":"uuid"}, "Canonical requestId changed"
    for verb, endpoint in (("get", "/api/v1/catalog/brands"),
                           ("get", "/api/v1/catalog/models"),
                           ("get", "/api/v1/catalog/models/{modelId}")):
        assert spec["paths"][endpoint][verb]["operationId"]
    security = spec["components"]["securitySchemes"]["localSession"]
    assert (security["type"],security["in"],security["name"]) == (
        "apiKey","cookie","rp_session"), "Opaque cookie session required"
    return len(ops)


if __name__ == "__main__":
    count = check_contract()
    print(f"Independent OpenAPI lint and contract gate OK: {count} reviewed operations")
