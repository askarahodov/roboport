"""Private work-order register contract and backward compatibility."""
import json
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker
from openapi_spec_validator import validate_spec
from roboport_api.main import app
from roboport_api.auto_order_overview_http import router

SPEC=json.loads((Path(__file__).resolve().parents[2]/
                 "contracts/auto-work-order-register-dev-openapi.json").read_text(encoding="utf-8"))
ID="11111111-1111-4111-8111-111111111111"


def valid(name,payload):
    return Draft202012Validator({**SPEC,"$ref":"#/components/schemas/"+name},
        format_checker=FormatChecker()).is_valid(payload)


def test_register_contract_is_private_and_matches_routes():
    validate_spec(SPEC)
    registered={(r.path,m) for r in router.routes for m in r.methods}
    assert len(SPEC["paths"])==2
    for path,operations in SPEC["paths"].items():
        assert (path,"GET") in registered
        assert path not in app.openapi()["paths"]
        assert operations["get"]["security"]==[{"localSession":[]}]
        assert operations["get"]["responses"]["200"]["headers"]["Cache-Control"]["schema"]["const"]=="no-store"


def test_legacy_and_workflow_shapes_and_hidden_document_enum():
    row={"workOrderId":ID,"appointmentId":ID,"bayId":ID,
         "createdAt":"2035-02-01T08:00:00Z","startsAt":"2035-02-01T09:00:00Z",
         "endsAt":"2035-02-01T10:00:00Z","stage":"READY"}
    assert valid("LegacyRow",row)
    detailed={**row,"repairStage":"READY","documentStage":"RESTRICTED",
              "bookingStatus":"SCHEDULED","localDay":"2035-02-01"}
    assert valid("WorkflowRow",detailed)
    assert not valid("LegacyRow",detailed)
    assert not valid("WorkflowRow",{**detailed,"documentStage":"PAID"})
    assert not valid("WorkflowRow",{**detailed,"customerId":ID})
    assert not valid("WorkflowRow",{**detailed,"localDay":"2035-02-30"})
    assert valid("WorkflowPage",{"items":[detailed],"nextCursor":None,
        "syntheticOnly":True,"releaseAllowed":False})
