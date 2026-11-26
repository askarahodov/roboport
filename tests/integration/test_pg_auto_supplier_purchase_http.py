"""Real FastAPI + PostgreSQL order→partial receipt→maker/checker, reject bad ACL."""
from uuid import uuid4
import json
from pathlib import Path
from jsonschema import Draft202012Validator, FormatChecker
import psycopg
import pytest
from fastapi.testclient import TestClient

from roboport_api.main import app
from test_pg_auto_supplier_purchase import environment
from test_pg_baseline import DSN
from test_pg_auto_stock_ledger import balance
from test_pg_auto_stock_lot_allocation import allocated


SPEC = json.loads((Path(__file__).resolve().parents[2] /
                   "contracts/auto-supplier-order-dev-openapi.json").read_text(encoding="utf-8"))


def assert_contract(response, name):
    assert response.headers["cache-control"] == "no-store"
    Draft202012Validator({**SPEC, "$ref": "#/components/schemas/" + name},
                         format_checker=FormatChecker()).validate(response.json())


def post(http,d,user,url,body,key=None):
    http.cookies.set("rp_session",d["tokens"][user])
    return http.post(url,json=body,headers={
        "Origin":"https://127.0.0.1",
        "X-CSRF-Token":d["csrf"][user],
        "Idempotency-Key":str(key or uuid4()),
    })


def fixture(monkeypatch):
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTO_API","1")
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTH_API","1")
    monkeypatch.setenv("ROBOport_DATABASE_URL",DSN)
    d,org,loc,item,lot,order,offers,grant_id,manager=environment(1)
    base=f"/api/v1/dev/auto/organizations/{org}/warehouse/supplier-orders"
    body={"locationId":str(loc),"workOrderId":str(order),"lotId":str(lot),
          "desiredQuantity":5,"offers":offers,
          "selectedSupplierId":"SYNTH-SUP-1","selectedOfferId":"OFFLINE-1"}
    return d,org,loc,item,lot,order,offers,grant_id,manager,base,body


def test_http_order_partial_receipts_auth_and_lifecycle(monkeypatch):
    d,org,loc,item,lot,order,offers,grant_id,manager,base,body=fixture(monkeypatch)
    with TestClient(app,base_url="https://127.0.0.1") as http:
        assert http.get(base,params={"locationId":str(loc)}).status_code==403
        http.cookies.set("rp_session",d["tokens"][d["client_a"]])
        assert http.get(base,params={"locationId":str(loc)}).status_code==403
        http.cookies.set("rp_session",d["tokens"][d["intake"]])
        empty=http.get(base,params={"locationId":str(loc)})
        assert empty.status_code==200 and empty.json()["items"]==[]
        assert empty.headers["cache-control"]=="no-store"
        key=uuid4()
        a=post(http,d,d["intake"],base,body,key)
        assert a.status_code==201,a.text
        assert_contract(a, "OrderReceipt")
        assert a.json()["orderedQuantity"]==4
        assert a.json()["syntheticOnly"] is True
        assert post(http,d,d["intake"],base,body,key).json()==a.json()
        assert post(http,d,d["intake"],base,{**body,"desiredQuantity":6},key).status_code==409
        ident=a.json()["orderId"]
        http.cookies.set("rp_session",d["tokens"][d["manager"]])
        listed=http.get(base,params={"locationId":str(loc)})
        assert listed.status_code==200
        assert_contract(listed, "OrderPage")
        assert listed.json()["items"][0]["orderId"]==ident
        assert listed.json()["items"][0]["outstandingQuantity"]==4
        detail_url=base+"/"+ident
        query={"locationId":str(loc)}
        detail=http.get(detail_url,params=query)
        assert detail.status_code==200,detail.text
        assert_contract(detail,"PurchaseDetail")
        assert detail.json()["offers"]==offers and detail.json()["deliveries"]==[]
        assert detail.json()["availableAtOrder"]==1 and detail.json()["desiredQuantity"]==5
        assert http.get(detail_url,params={"locationId":str(uuid4())}).status_code==403
        receipt_url=base+"/"+ident+"/receipts"
        one_data={"locationId":str(loc),"quantity":2,"supplierReference":"SUPPLIER-DELIVERY-001"}
        delivery_key=uuid4()
        created=post(http,d,d["intake"],receipt_url,one_data,delivery_key)
        assert created.status_code==201,created.text
        assert_contract(created, "DeliveryReceipt")
        assert created.json()["syntheticOnly"] is True
        assert post(http,d,d["intake"],receipt_url,one_data,delivery_key).json()==created.json()
        with psycopg.connect(DSN) as db:
            assert balance(db,item)==(1,0) and allocated(db,lot)==0
        # The physical receipt and cost approval stay in the existing dual-control flow.
        acceptance=post(http,d,d["manager"],
            f"/api/v1/dev/auto/organizations/{org}/warehouse/receipts/accept",
            {"locationId":str(loc),"documentId":created.json()["documentId"]})
        assert acceptance.status_code==201,acceptance.text
        # Manager receives *exact* immutable quote provenance in the existing
        # scoped valuation queue; the UI should not guess the source timestamp.
        http.cookies.set("rp_session",d["tokens"][d["manager"]])
        pending=http.get(
            f"/api/v1/dev/auto/organizations/{org}/warehouse/receipts/pending",
            params={"locationId":str(loc),"stage":"value"})
        assert pending.status_code==200,pending.text
        source=next(row["purchaseQuote"] for row in pending.json()["items"]
                    if row["documentId"]==created.json()["documentId"])
        assert source["orderId"]==ident
        assert source["supplierId"]=="SYNTH-SUP-1"
        assert source["currency"]=="RUB" and source["unitNetMinor"]==1200
        assert source["supplierPriceRevision"]==offers[0]["source_revision"]
        assert source["supplierQuotedAt"].replace("Z","+00:00")==offers[0]["quoted_at"]
        wrong=post(http,d,d["manager"],
            f"/api/v1/dev/auto/organizations/{org}/warehouse/receipts/valuation",
            {"locationId":str(loc),"documentId":created.json()["documentId"],
             "currency":"RUB","unitNetMinor":1201,"vatTotalMinor":240,
             "supplierPriceRevision":source["supplierPriceRevision"],
             "supplierQuotedAt":source["supplierQuotedAt"]})
        assert wrong.status_code==409,wrong.text
        valuation=post(http,d,d["manager"],
            f"/api/v1/dev/auto/organizations/{org}/warehouse/receipts/valuation",
            {"locationId":str(loc),"documentId":created.json()["documentId"],
             "currency":source["currency"],"unitNetMinor":source["unitNetMinor"],
             "vatTotalMinor":240,
             "supplierPriceRevision":source["supplierPriceRevision"],
             "supplierQuotedAt":source["supplierQuotedAt"]})
        assert valuation.status_code==201,valuation.text
        second=post(http,d,d["intake"],receipt_url,
              {"locationId":str(loc),"quantity":1,"supplierReference":"SUPPLIER-DELIVERY-002"})
        assert second.status_code==201,second.text
        cancel_url=base+"/"+ident+"/cancel"
        canceled=post(http,d,d["intake"],cancel_url,{"locationId":str(loc)})
        assert canceled.status_code==201,canceled.text
        assert_contract(canceled, "CancelReceipt")
        assert canceled.json()["canceledQuantity"]==1
        assert post(http,d,d["intake"],receipt_url,one_data).status_code==409
        assert post(http,d,d["intake"],cancel_url,{"locationId":str(loc)}).status_code==409
        http.cookies.set("rp_session",d["tokens"][d["intake"]])
        after=http.get(base,params={"locationId":str(loc)})
        assert after.status_code==200,after.text
        assert_contract(after, "OrderPage")
        line=after.json()["items"][0]
        assert (line["orderedQuantity"],line["documentedQuantity"],
                line["acceptedQuantity"],line["valuedQuantity"],
                line["canceledQuantity"],line["outstandingQuantity"])==(4,3,2,2,1,0)
        detail=http.get(detail_url,params={**query,"limit":"1"})
        assert detail.status_code==200,detail.text
        assert_contract(detail,"PurchaseDetail")
        first=detail.json()
        assert len(first["deliveries"])==1 and first["nextDeliveryId"]
        second_page=http.get(detail_url,params={**query,"limit":"1","afterDeliveryId":first["nextDeliveryId"]})
        assert second_page.status_code==200,second_page.text
        assert_contract(second_page,"PurchaseDetail")
        assert {x["stage"] for x in first["deliveries"]+second_page.json()["deliveries"]}=={"DRAFT","VALUED"}
        assert second_page.json()["nextDeliveryId"] is None
        for state,expected in (("CANCELED",1),("AWAITING_ACCEPTANCE",1),("OUTSTANDING",0),("COMPLETE",0)):
            filtered=http.get(base,params={**query,"state":state,"workOrderId":str(order)})
            assert filtered.status_code==200,filtered.text
            assert len(filtered.json()["items"])==expected
        assert http.get(base,params={**query,"state":"INVALID"}).status_code==422
        assert http.get(base,params={**query,"workOrderId":str(uuid4())}).json()["items"]==[]
        assert http.get(detail_url,params={**query,"afterDeliveryId":"bad"}).status_code==422
        from roboport_api.auto_document_access import publish_policy
        from test_pg_auto_document_access_http import admin
        with psycopg.connect(DSN) as db:
            admin(db,org,d["manager"])
            publish_policy(db,d["tokens"][d["manager"]],org,request_key=uuid4(),role="WAREHOUSE",loc=loc,user=None,
                document="SUPPLIER_ORDER",cap="PRICE_PURCHASE",allowed=False,not_before=None)
        hidden=http.get(detail_url,params=query)
        assert hidden.status_code==200,hidden.text
        assert_contract(hidden,"PurchaseDetail")
        assert hidden.json()["offers"] is None and hidden.json()["order"]["purchaseUnitMinor"] is None
        assert hidden.json()["order"]["purchasePriceHidden"] is True
        with psycopg.connect(DSN) as db:
            publish_policy(db,d["tokens"][d["manager"]],org,request_key=uuid4(),role="WAREHOUSE",loc=loc,user=None,
                document="SUPPLIER_ORDER",cap="PRICE_PURCHASE",allowed=True,not_before=None)
        with psycopg.connect(DSN) as db:
            assert balance(db,item)==(3,0) and allocated(db,lot)==2
            db.execute("UPDATE auto_organization_role_grant SET revoked_at=now() WHERE id=%s",
                       (grant_id,))
        assert post(http,d,d["intake"],receipt_url,one_data).status_code==403
        http.cookies.set("rp_session",d["tokens"][d["intake"]])
        assert http.get(base,params={"locationId":str(loc)}).status_code==403
        assert http.get(detail_url,params=query).status_code==403
        http.cookies.set("rp_session",d["tokens"][d["manager"]])
        assert http.get(base,params={"locationId":str(loc)}).status_code==200


def test_http_no_cross_scope_csrf_bad_quotes_and_gating(monkeypatch):
    d,org,loc,item,lot,order,offers,grant_id,manager,base,body=fixture(monkeypatch)
    with TestClient(app,base_url="https://127.0.0.1") as http:
        http.cookies.set("rp_session",d["tokens"][d["intake"]])
        assert http.post(base,json=body,headers={
            "Origin":"https://127.0.0.1",
            "Idempotency-Key":str(uuid4()),
        }).status_code==403
        assert http.post(base,json=body,headers={
            "Origin":"https://127.0.0.1",
            "X-CSRF-Token":d["csrf"][d["intake"]],
        }).status_code==422
        assert post(http,d,d["intake"],base,{**body,"desiredQuantity":True}).status_code==422
        assert post(http,d,d["intake"],base,{**body,"locationId":str(uuid4())}).status_code==403
        assert post(http,d,d["manager"],base,body).status_code==403
        assert post(http,d,d["intake"],base,{**body,"workOrderId":str(uuid4())}).status_code==403
        assert post(http,d,d["intake"],base,{**body,"lotId":str(uuid4())}).status_code==403
        assert post(http,d,d["intake"],base,{**body,"selectedSupplierId":"UNLISTED"}).status_code==422
        tampered=[dict(x) for x in offers]
        tampered[1]["source_revision"]="REV-BAD"
        tampered[1]["extra"]="unexpected"
        assert post(http,d,d["intake"],base,{**body,"offers":tampered}).status_code==422
        wrong_currency=[dict(x) for x in offers]
        wrong_currency[0]["currency"]="USD"
        assert post(http,d,d["intake"],base,{**body,"offers":wrong_currency}).status_code==422
        # Даже невыбранное предложение не может смешать валюты нового заказа.
        mixed = [dict(x) for x in offers]
        mixed[1]["currency"] = "USD"
        invalid = post(http,d,d["intake"],base,{**body,"offers":mixed})
        assert invalid.status_code == 422, invalid.text
        assert_contract(invalid, "Error")
        with psycopg.connect(DSN) as db:
            assert db.execute("SELECT count(*) FROM auto_supplier_purchase_order WHERE work_order_id=%s",
                              (order,)).fetchone() == (0,)
        for params in ({}, {"locationId":str(loc),"limit":"NaN"},
                       {"locationId":str(loc),"limit":"51"},
                       {"locationId":str(loc),"limit":"1.0"},
                       {"locationId":str(loc),"afterOrderId":"bad"},
                       [("locationId",str(loc)),("limit","1"),("limit","2")],
                       [("locationId",str(loc)),("locationId",str(uuid4()))],
                       [("locationId",str(loc)),("afterOrderId",str(uuid4())),
                        ("afterOrderId",str(uuid4()))]):
            invalid = http.get(base, params=params)
            assert invalid.status_code == 422, invalid.text
            assert_contract(invalid, "Error")
        q=http.get(base,params={"locationId":str(uuid4())})
        assert q.status_code==403
        monkeypatch.setenv("ROBOport_SYNTHETIC_AUTO_API","0")
        assert post(http,d,d["intake"],base,body).status_code==404
        assert http.get(base,params={"locationId":str(loc)}).status_code==404
