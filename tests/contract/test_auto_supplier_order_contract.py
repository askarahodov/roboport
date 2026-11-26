"""Закрытый контракт закупки, отдельно от публичного OpenAPI."""
import json
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker
from openapi_spec_validator import validate_spec
from roboport_api.main import app
from roboport_api.auto_supplier_purchase_http import router

SPEC = json.loads((Path(__file__).resolve().parents[2] /
                   "contracts/auto-supplier-order-dev-openapi.json").read_text(encoding="utf-8"))
BASE = "/api/v1/dev/auto/organizations/{org_id}/warehouse/supplier-orders"
ID = "11111111-1111-4111-8111-111111111111"


def valid(name, payload):
    return Draft202012Validator({**SPEC, "$ref": "#/components/schemas/" + name},
                                format_checker=FormatChecker()).is_valid(payload)


def test_private_routes_security_and_response_contract():
    validate_spec(SPEC)
    assert SPEC["x-synthetic-only"] is True
    expected = {(BASE, "GET"), (BASE, "POST"), (BASE + "/{order_id}", "GET"),
                (BASE + "/{order_id}/receipts", "POST"),
                (BASE + "/{order_id}/cancel", "POST")}
    assert {(p, m.upper()) for p, ops in SPEC["paths"].items() for m in ops} == expected
    assert {(route.path, m) for route in router.routes for m in route.methods} == expected
    for path, method in expected:
        assert path not in app.openapi()["paths"]
        op = SPEC["paths"][path][method.lower()]
        assert op["security"] == [{"localSession": []}]
        if method == "POST":
            assert {p["name"] for p in op["parameters"] if p["in"] == "header"
                    and p["required"]} == {"Idempotency-Key", "X-CSRF-Token"}
            assert "409" in op["responses"]
        for response in op["responses"].values():
            assert response["headers"]["Cache-Control"]["schema"]["const"] == "no-store"


def test_offer_and_mutation_shapes():
    quote = {"offer_id": "A", "supplier_id": "S", "source_revision": "R",
             "quoted_at": "2026-11-26T00:00:00Z", "expires_at": "2026-11-27T00:00:00Z",
             "currency": "RUB", "unit_purchase_minor": 1200, "available_quantity": 3,
             "article": "SKU", "delivery_days": 2}
    payload = {"locationId": ID, "workOrderId": ID, "lotId": ID,
               "desiredQuantity": 5, "offers": [quote, {**quote, "supplier_id": "T"}],
               "selectedSupplierId": "S", "selectedOfferId": "A"}
    assert valid("Order", payload)
    assert not valid("Order", {**payload, "desiredQuantity": True})
    assert not valid("Order", {**payload, "offers": [quote]})
    assert not valid("Offer", {**quote, "applicability": "VERIFIED"})
    assert not valid("Offer", {**quote, "unit_purchase_minor": -1})
    assert not valid("Offer", {**quote, "quoted_at": "2026-11-26T00:00:00"})
    assert valid("Delivery", {"locationId": ID, "quantity": 1, "supplierReference": "RMA"})
    assert not valid("Delivery", {"locationId": ID, "quantity": 0, "supplierReference": "RMA"})
    assert valid("Cancel", {"locationId": ID})
    assert not valid("Cancel", {"locationId": ID, "quantity": 1})


def test_price_redaction_and_page_contract():
    row = {"orderId": ID, "workOrderId": ID, "lotId": ID, "article": "SKU",
           "orderedQuantity": 4, "supplierId": "S", "offerId": "A", "quoteRevision": "R",
           "purchaseUnitMinor": 1200, "purchasePriceHidden": False, "currency": "RUB",
           "documentedQuantity": 3, "acceptedQuantity": 2, "valuedQuantity": 2,
           "canceledQuantity": 1, "outstandingQuantity": 0}
    page = {"syntheticOnly": True, "items": [row], "nextOrderId": None}
    assert valid("OrderPage", page)
    assert valid("OrderRow", {**row, "purchaseUnitMinor": None, "purchasePriceHidden": True})
    assert not valid("OrderRow", {**row, "purchasePriceHidden": True})
    assert not valid("OrderRow", {**row, "purchaseUnitMinor": None})
    assert not valid("OrderRow", {**row, "sellingPriceMinor": 1})
    assert not valid("OrderPage", {**page, "items": [row] * 51})
