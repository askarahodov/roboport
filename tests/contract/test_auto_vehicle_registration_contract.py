"""Частный OpenAPI регистрации: проверка маршрутов и DTO, не production."""
import json
from pathlib import Path
from jsonschema import Draft202012Validator, FormatChecker
from openapi_spec_validator import validate_spec
from roboport_api.main import app
from roboport_api.auto_vehicle_claim_http import router

SPEC=json.loads((Path(__file__).resolve().parents[2]/"contracts/auto-vehicle-registration-dev-openapi.json").read_text(encoding="utf-8"))
BASE="/api/v1/dev/auto/organizations/{org_id}/vehicle-registrations"


def valid(name,payload):
    return Draft202012Validator({**SPEC,"$ref":"#/components/schemas/"+name},
                                format_checker=FormatChecker()).is_valid(payload)


def test_registration_private_routes():
    validate_spec(SPEC)
    assert SPEC["x-synthetic-only"] is True
    routes={(BASE,"POST"),(BASE+"/{vehicle_id}","GET"),(BASE+"/{vehicle_id}/revisions","POST")}
    assert {(path,method.upper()) for path,ops in SPEC["paths"].items() for method in ops}==routes
    assert {(route.path,method) for route in router.routes for method in route.methods
            if route.path.startswith(BASE)}==routes
    for path,method in routes:
        assert path not in app.openapi()["paths"]
        op=SPEC["paths"][path][method.lower()]
        assert op["security"]==[{"localSession":[]}]
        if method=="POST":
            assert {p["name"] for p in op["parameters"] if p["in"]=="header" and p["required"]}=={"Idempotency-Key","X-CSRF-Token"}
        status="201" if path==BASE else "200"
        assert op["responses"][status]["headers"]["Cache-Control"]["schema"]["const"]=="no-store"


def test_registration_payload_and_no_vin_leak():
    ident="11111111-1111-4111-8111-111111111111"
    create={"locationId":ident,"customerId":ident,"make":"Synthetic","model":"Test"}
    assert valid("Create",create)
    assert valid("Create",{**create,"vin":"SYN12345678901234","modelYear":2026})
    assert not valid("Create",{**create,"verified":True})
    assert not valid("Create",{**create,"modelYear":True})
    edit={"locationId":ident,"make":"Synthetic","model":"Test","expectedRevision":1}
    assert valid("Edit",edit)
    assert not valid("Edit",{**edit,"vin":"SYN12345678901234"})
    assert not valid("Edit",{**edit,"customerId":ident})
    assert not valid("Edit",{**edit,"expectedRevision":0})
    result={"vehicleId":ident,"linkId":ident,"revision":1,"status":"PENDING"}
    assert valid("Receipt",result)
    assert not valid("Receipt",{**result,"status":"VERIFIED"})
    detail={"vehicleId":ident,"revision":1,"make":"Synthetic","model":"Test","variant":None,
            "modelYear":2026,"vinLast4":"1234","editable":True}
    assert valid("Details",detail)
    assert not valid("Details",{**detail,"vin":"SYN12345678901234"})
    assert not valid("Details",{**detail,"proof":"synthetic"})
