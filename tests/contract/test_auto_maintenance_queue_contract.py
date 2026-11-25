"""Private maintenance queue contract, separate from public OpenAPI."""
import json
from pathlib import Path
from jsonschema import Draft202012Validator,FormatChecker
from openapi_spec_validator import validate_spec
from roboport_api.main import app

SPEC=json.loads((Path(__file__).resolve().parents[2]/"contracts/auto-maintenance-queue-dev-openapi.json").read_text(encoding="utf-8"))
PATH="/api/v1/dev/auto/organizations/{org_id}/locations/{loc_id}/crm/maintenance-queue"

def test_queue_contract_is_private_and_bounded():
    validate_spec(SPEC)
    assert SPEC["x-synthetic-only"] is True
    assert set(SPEC["paths"])=={PATH}
    op=SPEC["paths"][PATH]["get"]
    assert op["security"]==[{"localSession":[]}]
    assert set(op["responses"])=={"200","403","404","422"}
    assert op["responses"]["200"]["headers"]["Cache-Control"]["schema"]["const"]=="no-store"
    assert PATH not in app.openapi()["paths"]
    assert any(getattr(r,"path",None)==PATH for r in app.routes)

def test_queue_dto_no_customer_data_or_unbounded_page():
    check=Draft202012Validator({**SPEC,"$ref":"#/components/schemas/MaintenanceQueue"},format_checker=FormatChecker())
    ident="11111111-1111-4111-8111-111111111111"
    row={"recommendationId":ident,"workOrderId":ident,"serviceCode":"TO","title":"ТО",
         "reasonCode":"PERIODIC","baselineKm":100,"dueDate":"2030-01-02","dueKm":200,"status":"UPCOMING"}
    page={"syntheticOnly":True,"sendAllowed":False,"basis":"UNBOOKED_ACTIVE_DUE_DATE",
          "organizationId":ident,"locationId":ident,"localDay":"2030-01-01","timeZone":"Europe/Berlin",
          "mode":"ALL","horizonDays":30,"reasonCode":"ALL","items":[row],"nextCursor":None}
    assert check.is_valid(page)
    assert not check.is_valid({**page,"items":[row]*26})
    assert not check.is_valid({**page,"sendAllowed":True})
    assert not check.is_valid({**page,"nextCursor":"a"*257})
    assert not check.is_valid({**page,"items":[{**row,"phone":"synthetic"}]})
    assert not check.is_valid({**page,"horizonDays":8})
