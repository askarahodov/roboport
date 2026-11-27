import json
from pathlib import Path
from jsonschema import Draft202012Validator,FormatChecker
from openapi_spec_validator import validate_spec
from roboport_api.main import app
from roboport_api.auto_closed_order_report import router,PREFIX

SPEC=json.loads((Path(__file__).resolve().parents[2]/"contracts/auto-closed-order-comparison-dev-openapi.json").read_text(encoding="utf-8"))


def test_comparison_private_contract():
    validate_spec(SPEC)
    from scripts.check_openapi_inventory import private_boundary
    assert private_boundary(SPEC) == []
    for path in (PREFIX+"/comparison",PREFIX+"/comparison/export"):
        assert path not in app.openapi()["paths"]
        assert [(r.path,m) for r in router.routes for m in r.methods if r.path==path]==[(path,"GET")]
    check=Draft202012Validator({**SPEC,"$ref":"#/components/schemas/Comparison"},format_checker=FormatChecker())
    fields=("orderCount","uniqueCustomers","singleOrderCustomers","multipleOrderCustomers",
            "additionalClosedOrders","approvedTotalMinor","simulatedPaidMinor","simulatedRefundedMinor")
    delta=dict.fromkeys(fields,0)
    def empty(day):
        return {**delta,"fromDay":day,"toDay":day,"multipleOrderBasisPoints":None,
                "days":[{"day":day,"closedOrders":0}]}
    value={"syntheticOnly":True,"realMoney":False,"releaseAllowed":False,
           "retentionAvailable":False,"actualVisitsAvailable":False,"currency":"RUB",
           "basis":"RECONCILED_CLOSED_ORDER_PERIOD_COMPARISON","timeZone":"Europe/Berlin",
           "current":empty("2030-10-27"),"previous":empty("2030-10-26"),"delta":delta}
    assert check.is_valid(value)
    assert not check.is_valid({**value,"realMoney":True})
    assert not check.is_valid({**value,"current":{**value["current"],"customerId":"private"}})
    assert not check.is_valid({**value,"current":{**value["current"],"days":[]}})
