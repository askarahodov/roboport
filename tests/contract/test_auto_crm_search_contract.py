import json
from pathlib import Path
from jsonschema import Draft202012Validator,FormatChecker
from openapi_spec_validator import validate_spec
from roboport_api.main import app
from roboport_api.auto_crm_leads_http import router

SPEC=json.loads((Path(__file__).resolve().parents[2]/"contracts/auto-crm-search-dev-openapi.json").read_text(encoding="utf-8"))
PATH="/api/v1/dev/auto/organizations/{org_id}/locations/{loc_id}/crm/lead-search"

def test_search_contract_private_and_bounded():
    validate_spec(SPEC)
    assert PATH not in app.openapi()["paths"]
    assert [(r.path,m) for r in router.routes for m in r.methods if r.path==PATH]==[(PATH,"GET")]
    check=Draft202012Validator({**SPEC,"$ref":"#/components/schemas/SearchPage"},format_checker=FormatChecker())
    ident="11111111-1111-4111-8111-111111111111"
    row={"leadId":ident,"customerLinkId":ident,"ownerId":ident,"sourceCode":"WEB","status":"NEW",
         "createdAt":"2030-01-01T10:00:00Z","visitNumber":1}
    page={"syntheticOnly":True,"organizationId":ident,"locationId":ident,"customerLinkId":None,
          "sourceCode":"ALL","stageCode":"ALL","items":[row],"nextCursor":None}
    assert check.is_valid(page)
    assert not check.is_valid({**page,"items":[row]*26})
    assert not check.is_valid({**page,"items":[{**row,"phone":"private"}]})
    assert not check.is_valid({**page,"nextCursor":"a"*257})
