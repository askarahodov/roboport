"""Контракт закрытых тарифов и комплексов; без production API."""
import json
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker
from openapi_spec_validator import validate_spec
from roboport_api.main import app
from roboport_api.auto_work_tariffs_http import router as tariffs
from roboport_api.auto_work_complexes_http import router as complexes

SPEC = json.loads((Path(__file__).resolve().parents[2] /
                   "contracts/auto-work-pricing-dev-openapi.json").read_text(encoding="utf-8"))
ID = "11111111-1111-4111-8111-111111111111"


def valid(name, payload):
    return Draft202012Validator({**SPEC, "$ref": "#/components/schemas/" + name},
                                format_checker=FormatChecker()).is_valid(payload)


def test_routes_match_private_routers_and_mutation_contract():
    validate_spec(SPEC)
    expected = {(route.path, method) for router in (tariffs, complexes)
                for route in router.routes for method in route.methods}
    assert len(expected) == 7
    assert {(p, m.upper()) for p, ops in SPEC["paths"].items() for m in ops} == expected
    assert SPEC["x-synthetic-only"] is True
    for path, method in expected:
        assert path not in app.openapi()["paths"]
        op = SPEC["paths"][path][method.lower()]
        assert op["security"] == [{"localSession": []}]
        if method == "POST":
            assert {p["name"] for p in op["parameters"] if p["in"] == "header"
                    and p["required"]} == {"X-CSRF-Token"}
            assert {"200", "201", "409"} <= set(op["responses"])
        for response in op["responses"].values():
            assert response["headers"]["Cache-Control"]["schema"]["const"] == "no-store"


def test_tariff_inputs_and_mutation_shapes():
    proposal = {"requestKey": ID, "expectedVersion": 0, "qualificationCode": "MASTER",
                "rateMinorPerHour": 10000, "maxDiscountBps": 2500}
    assert valid("TariffProposal", proposal)
    assert not valid("TariffProposal", {**proposal, "maxDiscountBps": 2501})
    assert not valid("TariffProposal", {**proposal, "expectedVersion": True})
    assert not valid("TariffProposal", {**proposal, "qualificationCode": "ANY"})
    assert not valid("Approval", {"requestKey": ID, "approvedBy": ID})
    state = {"items": [], "active": {}, "syntheticOnly": True}
    assert valid("TariffState", state)
    assert valid("TariffMutation", {**state, "ok": True, "replayed": False})
    assert not valid("TariffState", {**state, "syntheticOnly": False})


def test_complex_consumables_and_bounds():
    material = {"skuId": ID, "itemKind": "CONSUMABLE", "quantity": 1, "unitPriceMinor": 0}
    payload = {"requestKey": ID, "code": "SYNTHETIC", "title": "Комплекс",
               "serviceIds": [ID], "materials": [material]}
    assert valid("ComplexCreate", payload)
    assert not valid("ComplexCreate", {**payload, "serviceIds": [ID, ID]})
    assert not valid("ComplexCreate", {**payload, "serviceIds": []})
    assert not valid("MaterialInput", {**material, "unitPriceMinor": 1})
    assert not valid("MaterialInput", {**material, "quantity": True})
    assert not valid("MaterialInput", {**material, "purchasePriceMinor": 5})
    assert valid("Select", {"complexId": ID, "requestKey": ID})
    assert not valid("Select", {"complexId": ID, "requestKey": ID, "orderId": ID})
    assert not valid("Selections", {"items": [], "syntheticOnly": False})
