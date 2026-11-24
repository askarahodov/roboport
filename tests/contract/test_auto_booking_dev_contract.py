"""Контракт всех частных маршрутов записи; никогда не публикуется в production."""
import json
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker
from openapi_spec_validator import validate_spec

from roboport_api.main import app
from roboport_api.auto_appointment_http import router as staff_router
from roboport_api.auto_customer_booking_http import router as customer_router

SPEC=json.loads((Path(__file__).resolve().parents[2] /
                 "contracts/auto-booking-dev-openapi.json").read_text(encoding="utf-8"))
ID="11111111-1111-4111-8111-111111111111"


def validator(name):
    return Draft202012Validator({**SPEC,"$ref":"#/components/schemas/"+name},
                                format_checker=FormatChecker())


def test_booking_contract_routes_private_and_complete():
    validate_spec(SPEC)
    assert SPEC["x-synthetic-only"] is True
    assert SPEC["servers"]==[{"url":"https://127.0.0.1"}]
    actual={(route.path,method.lower()) for router in (staff_router,customer_router)
            for route in router.routes for method in route.methods}
    declared={(path,method) for path,operations in SPEC["paths"].items()
              for method in operations}
    assert actual==declared
    for path,method in declared:
        assert path not in app.openapi()["paths"]
        op=SPEC["paths"][path][method]
        assert op["security"]==[{"localSession":[]}]
        assert {"403","404","422"}<=set(op["responses"])
        success=op["responses"]["201" if "201" in op["responses"] else "200"]
        assert success["headers"]["Cache-Control"]["schema"]["const"]=="no-store"


def test_booking_contract_bounds_and_no_private_fields():
    item={"id":ID,"label":"SYNTHETIC WORK","durationMinutes":30,"available":None}
    valid={"syntheticOnly":True,"items":[item],"nextAfterId":None}
    assert validator("DirectoryPage").is_valid(valid)
    for bad in ({**item,"price":100},{**item,"vin":"FULL"},
                {**item,"durationMinutes":0}):
        assert not validator("DirectoryPage").is_valid({**valid,"items":[bad]})
    assert not validator("DirectoryPage").is_valid({**valid,"items":[item]*51})
    history={"syntheticOnly":True,"deliveryChannel":"DEV_SINK","realDelivery":False,
             "appointmentId":ID,"workOrderId":ID,"timeZone":"Europe/Berlin",
             "items":[],"cancellation":None,"nextCursor":None}
    assert validator("HistoryPage").is_valid(history)
    assert not validator("HistoryPage").is_valid({**history,"realDelivery":True})
    assert not validator("HistoryPage").is_valid({**history,"phone":"PRIVATE"})
    body={"locationId":ID,"bayId":ID,"customerLinkId":ID,"complexId":ID,
          "startsAt":"2030-01-01T10:00:00Z","endsAt":"2030-01-01T11:00:00Z"}
    assert validator("StaffCreate").is_valid(body)
    assert not validator("StaffCreate").is_valid({**body,"serviceIds":[ID]})
    assert not validator("StaffCreate").is_valid({**body,"materialItems":[
        {"skuId":ID,"kind":"PART","quantity":1}]})
    customer={k:v for k,v in body.items() if k!="complexId"}
    customer["organizationId"]=ID
    assert validator("CustomerCreate").is_valid(customer)
    assert not validator("CustomerCreate").is_valid({**customer,"employeeId":ID})
