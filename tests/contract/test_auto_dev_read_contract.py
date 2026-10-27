"""Независимый OpenAPI 3.1 gate для закрытого автомобильного read API."""
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator, FormatChecker
from openapi_spec_validator import validate_spec

from roboport_api.main import app

SPEC = yaml.safe_load(
    (Path(__file__).resolve().parents[2] / "contracts/auto-dev-openapi.yaml")
    .read_text(encoding="utf-8")
)
LIST_PATH = "/api/v1/dev/auto/vehicles"
DETAIL_PATH = "/api/v1/dev/auto/vehicles/{vehicle_id}"


def check_auto_dev_spec(spec=SPEC):
    validate_spec(spec)
    assert spec["openapi"].startswith("3.1.")
    assert spec["x-synthetic-only"] is True
    assert [server["url"] for server in spec["servers"]] == ["https://127.0.0.1"]
    assert set(spec["paths"]) == {LIST_PATH, DETAIL_PATH}
    assert all(set(spec["paths"][path]) == {"get"} for path in spec["paths"])
    assert {
        spec["paths"][LIST_PATH]["get"]["operationId"],
        spec["paths"][DETAIL_PATH]["get"]["operationId"],
    } == {"listVerifiedAutoDevVehicles", "getVerifiedAutoDevVehicle"}
    for path in (LIST_PATH, DETAIL_PATH):
        operation = spec["paths"][path]["get"]
        assert operation["security"] == [{"localSession": []}]
        assert {"200", "401", "403", "404"} <= set(operation["responses"])
        assert operation["responses"]["200"]["headers"]["Cache-Control"]["schema"]["const"] == "no-store"
    assert "422" in spec["paths"][LIST_PATH]["get"]["responses"]
    paged = spec["paths"][LIST_PATH]["get"]["parameters"]
    params = {param["name"]: param["schema"] for param in paged}
    assert params["limit"]["minimum"] == 1 and params["limit"]["maximum"] == 50
    assert params["offset"]["minimum"] == 0 and params["offset"]["maximum"] == 10000
    vehicle = spec["components"]["schemas"]["AutoVehicle"]
    assert set(vehicle["required"]) == {"id", "make", "model", "variant", "modelYear", "vinLast4"}
    assert vehicle["additionalProperties"] is False
    assert "vin" not in vehicle["properties"]
    assert vehicle["properties"]["vinLast4"]["pattern"] == "^[A-HJ-NPR-Z0-9]{4}$"
    assert spec["components"]["schemas"]["AutoVehiclePage"]["additionalProperties"] is False
    cookie = spec["components"]["securitySchemes"]["localSession"]
    assert (cookie["type"], cookie["in"], cookie["name"]) == ("apiKey", "cookie", "rp_session")


def test_auto_dev_contract_validation_and_nonpublication():
    check_auto_dev_spec()
    # Это отдельная dev-схема для QA и типизации, но не публичный FastAPI contract.
    assert LIST_PATH not in app.openapi()["paths"]
    assert DETAIL_PATH not in app.openapi()["paths"]


def test_auto_dev_json_schema_rejects_full_vin_and_invalid_page():
    schema = {**SPEC, "$ref": "#/components/schemas/AutoVehiclePage"}
    check = Draft202012Validator(schema, format_checker=FormatChecker())
    item = {
        "id": "11111111-1111-4111-8111-111111111111",
        "make": "Synthetic", "model": "Van", "variant": None,
        "modelYear": 2026, "vinLast4": "1234",
    }
    assert check.is_valid({"items": [item]})
    assert check.is_valid({"items": []})
    assert not check.is_valid({"items": [{**item, "vin": "SYNTHETICVIN00001"}]})
    assert not check.is_valid({"items": [{**item, "vinLast4": "12345"}]})
    assert not check.is_valid({"items": [{**item, "modelYear": 1800}]})
    assert not check.is_valid({"items": [item], "customerId": "other"})
