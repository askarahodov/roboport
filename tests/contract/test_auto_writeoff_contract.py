"""Частный OpenAPI актов списания, включая серийное выбытие."""
import json
from pathlib import Path
from jsonschema import Draft202012Validator,FormatChecker
from openapi_spec_validator import validate_spec
from roboport_api.main import app
from roboport_api.auto_stocktake_http import router

SPEC=json.loads((Path(__file__).resolve().parents[2]/"contracts/auto-writeoff-dev-openapi.json").read_text(encoding="utf-8"))
BASE="/api/v1/dev/auto/organizations/{org_id}/warehouse/writeoffs"


def valid(name,payload):
    return Draft202012Validator({**SPEC,"$ref":"#/components/schemas/"+name},
                                format_checker=FormatChecker()).is_valid(payload)


def test_private_writeoff_contract():
    validate_spec(SPEC)
    assert SPEC["x-synthetic-only"] is True
    routes={(BASE,"GET"),(BASE,"POST"),(BASE+"/{document_id}/decisions","POST")}
    assert {(path,method.upper()) for path,ops in SPEC["paths"].items() for method in ops}==routes
    assert {(route.path,method) for route in router.routes for method in route.methods
            if route.path.startswith(BASE)}==routes
    assert not any(path in app.openapi()["paths"] for path,method in routes)
    ident="11111111-1111-4111-8111-111111111111"
    body={"locationId":ident,"lotId":ident,"quantity":1,"reasonCode":"DAMAGED","note":"Брак"}
    assert valid("Draft",body)
    assert valid("Draft",{**body,"serialIds":[ident]})
    assert not valid("Draft",{**body,"serialIds":[ident,ident]})
    assert not valid("Draft",{**body,"serialIds":["bad"]})
    assert not valid("Draft",{**body,"quantity":True})
    assert not valid("Decision",{"locationId":ident,"action":"APPROVE","note":"Осмотрено","serialIds":[ident]})
