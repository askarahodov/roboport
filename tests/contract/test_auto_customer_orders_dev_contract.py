"""Private client quote/history contract, not a public mobile API."""
import json
from pathlib import Path
from jsonschema import Draft202012Validator,FormatChecker
from openapi_spec_validator import validate_spec
from roboport_api.main import app
from roboport_api.auto_customer_orders_http import router
from roboport_api.auto_work_order_estimate_http import router as estimate_router

SPEC=json.loads((Path(__file__).resolve().parents[2]/"contracts/auto-customer-orders-dev-openapi.json").read_text(encoding="utf-8"))


def valid(name,body):
    return Draft202012Validator({**SPEC,"$ref":"#/components/schemas/"+name},
        format_checker=FormatChecker()).is_valid(body)


def test_private_customer_contract_matches_existing_routes():
    validate_spec(SPEC)
    assert SPEC["x-synthetic-only"] is True
    pairs={(p,m.upper()) for p,ops in SPEC["paths"].items() for m in ops}
    registered={(r.path,m) for rt in (router,estimate_router) for r in rt.routes for m in r.methods}
    assert len(pairs)==4 and pairs<=registered
    assert not any(p in app.openapi()["paths"] for p,m in pairs)
    ident="11111111-1111-4111-8111-111111111111"
    decision={"revisionId":ident,"requestKey":ident,"decision":"APPROVED"}
    assert valid("Decision",decision)
    assert not valid("Decision",{**decision,"customerId":ident})
    assert not valid("Decision",{**decision,"decision":"YES"})
    assert valid("Clarification",{"revisionId":ident,"requestKey":ident,"reasonCode":"PRICE"})
    assert not valid("Clarification",{"revisionId":ident,"requestKey":ident,"reasonCode":"free text"})
    card={"id":ident,"organizationId":ident,"locationId":ident,"createdAt":"2030-01-01T10:00:00Z",
          "vehicle":{"make":"Synthetic","model":"Test","modelYear":2026,"vinLast4":"1234"},
          "appointment":{"startsAt":"2030-01-01T10:00:00Z","endsAt":"2030-01-01T11:00:00Z","status":"SCHEDULED"}}
    assert valid("Card",card)
    assert not valid("Card",{**card,"vehicle":{**card["vehicle"],"vin":"SYNTHETIC"}})
    assert not valid("Card",{**card,"vehicle":{**card["vehicle"],"vinLast4":"I123"}})
