"""Закрытый контракт возврата поставщику, отдельно от production OpenAPI."""
import json
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker
from openapi_spec_validator import validate_spec
from roboport_api.main import app
from roboport_api.auto_stocktake_http import router

SPEC=json.loads((Path(__file__).resolve().parents[2]/"contracts/auto-supplier-return-dev-openapi.json").read_text(encoding="utf-8"))
BASE="/api/v1/dev/auto/organizations/{org_id}/warehouse/supplier-returns"


def test_supplier_return_contract_is_private_and_matches_routes():
    validate_spec(SPEC)
    assert SPEC["x-synthetic-only"] is True
    expected={(BASE,"POST"),(BASE+"/approvals","POST"),(BASE+"/decisions","POST"),(BASE+"/pending","GET")}
    assert {(path,method.upper()) for path,ops in SPEC["paths"].items() for method in ops}==expected
    assert {(route.path,method) for route in router.routes for method in route.methods
            if route.path.startswith(BASE)}==expected
    for path,method in expected:
        assert path not in app.openapi()["paths"]
        op=SPEC["paths"][path][method.lower()]
        assert op["security"]==[{"localSession":[]}]
        if method=="POST":
            assert {p["name"] for p in op["parameters"] if p["in"]=="header" and p["required"]}=={"Idempotency-Key","X-CSRF-Token"}
        code="201" if method=="POST" else "200"
        assert op["responses"][code]["headers"]["Cache-Control"]["schema"]["const"]=="no-store"


def valid(schema,payload):
    return Draft202012Validator({**SPEC,"$ref":"#/components/schemas/"+schema},
                                format_checker=FormatChecker()).is_valid(payload)


def test_supplier_return_decision_and_pending_dto():
    ident="11111111-1111-4111-8111-111111111111"
    decision={"locationId":ident,"documentId":ident,"action":"CANCEL","note":"Ошибочный акт"}
    assert valid("Decision",decision)
    assert valid("Decision",{**decision,"action":"REJECT"})
    assert not valid("Decision",{**decision,"action":"APPROVE"})
    assert not valid("Decision",{**decision,"note":"a"})
    assert not valid("Decision",{**decision,"actorId":ident})
    item={"documentId":ident,"sourceReceiptId":ident,"lotId":ident,"article":"SKU",
          "batchCode":"LOT","binCode":"BIN","quantity":1,"reason":"Брак","supplierReference":"RMA"}
    page={"syntheticOnly":True,"items":[item],"nextDocumentId":None}
    assert valid("PendingPage",page)
    assert not valid("PendingPage",{**page,"items":[item]*51})
    assert not valid("PendingPage",{**page,"items":[{**item,"createdBy":ident}]})
    assert not valid("PendingPage",{**page,"items":[{**item,"quantity":True}]})


def test_canceled_return_cannot_have_financial_posting():
    ident="11111111-1111-4111-8111-111111111111"
    ts="2026-11-26T06:00:00Z"
    row={"documentId":ident,"createdAt":ts,"sourceReceiptId":ident,"lotId":ident,
         "quantity":"2","supplierReference":"RMA","reason":"Брак","status":"CANCELED",
         "acceptanceId":None,"acceptedAt":None,"dispositionId":None,"attribution":None,
         "decision":{"id":ident,"note":"Ошибочный акт","createdAt":ts}}
    assert valid("ReconciliationReturn",row)
    assert not valid("ReconciliationReturn",{**row,"attribution":{"currency":"RUB","netMinor":"1","vatMinor":"0"}})
    assert not valid("ReconciliationReturn",{**row,"dispositionId":ident})
    assert not valid("ReconciliationReturn",{**row,"status":"DRAFT"})
