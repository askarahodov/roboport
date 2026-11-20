"""PG17/HTTPS end-to-end stage checklist for one scoped automotive WorkOrder."""
from uuid import uuid4

import psycopg
import pytest
from roboport_api.auto_order_workspace import read_order_workspace
from roboport_api.auto_stock_service import AutoStockDenied
from test_pg_baseline import DSN
from test_pg_auto_work_order_register_http import setup
from roboport_api.auto_document_access import publish_policy


def test_workspace_reads_actual_stages_not_price_or_personal_data(monkeypatch):
    d,org,loc,foreign,foreign_loc,orders,client,_=setup(monkeypatch,count=1)
    order=orders[0]
    url=(f"/api/v1/dev/auto/organizations/{org}/locations/{loc}"
         f"/orders/{order}/workspace")
    assert client.get(url).status_code==403
    client.cookies.set("rp_session",d["tokens"][d["intake"]])
    response=client.get(url)
    assert response.status_code==200,response.text
    assert response.headers["cache-control"]=="no-store"
    assert response.headers["x-content-type-options"]=="nosniff"
    expected={
        "services":"EMPTY","inspection":"MISSING","intakeAct":"MISSING",
        "estimate":"RESTRICTED","execution":"NOT_STARTED","quality":"NOT_CHECKED",
        "technicalDocuments":"RESTRICTED",
    }
    payload=response.json()
    assert payload=={
        "workOrderId":str(order),"syntheticOnly":True,"realMoney":False,
        "releaseAllowed":False,"bookingCanceled":False,"steps":expected,
    }
    for forbidden in (str(d["vehicle"]),str(d["link"]),str(d["manager"]),
                      "customerId","vehicleId","customerLinkId",
                      "amountMinor","price","paid","token"):
        assert forbidden not in response.text
    # An actual immutable PG17 inspection moves the corresponding stage only.
    with psycopg.connect(DSN) as db:
        db.execute("""INSERT INTO auto_work_order_inspection
            (id,work_order_id,organization_id,location_id,actor_id,
             odometer_km,fuel_percent,tyres_ok,lights_ok,fluid_leak,exterior_code)
            VALUES(%s,%s,%s,%s,%s,100,40,TRUE,TRUE,FALSE,'CLEAR')""",
            (uuid4(),order,org,loc,d["intake"]))
    inspected=client.get(url)
    assert inspected.status_code==200,inspected.text
    assert inspected.json()["steps"]=={**expected,"inspection":"RECORDED"}
    with psycopg.connect(DSN) as db:
        booking=db.execute("""SELECT a.booking_id FROM auto_work_order w
                              JOIN auto_appointment a ON a.id=w.appointment_id
                              WHERE w.id=%s""",(order,)).fetchone()[0]
        db.execute("""UPDATE auto_bay_booking SET canceled_at=now()
                      WHERE id=%s""",(booking,))
    canceled=client.get(url)
    assert canceled.status_code==200,canceled.text
    assert canceled.json()["bookingCanceled"] is True
    assert canceled.json()["releaseAllowed"] is False


def test_workspace_denies_foreign_scope_revoked_grant_and_proof(monkeypatch):
    d,org,loc,foreign,foreign_loc,orders,client,_=setup(monkeypatch,count=1)
    order=orders[0]
    url=(f"/api/v1/dev/auto/organizations/{org}/locations/{loc}"
         f"/orders/{order}/workspace")
    client.cookies.set("rp_session",d["tokens"][d["intake"]])
    assert client.get(url).status_code==200
    assert client.get(url.replace(str(org),str(foreign))).status_code==403
    assert client.get(url.replace(str(loc),str(foreign_loc))).status_code==403
    assert client.get(url.replace(str(order),str(uuid4()))).status_code==403
    assert client.get(url.replace(str(order),"not-a-uuid")).status_code==422
    with psycopg.connect(DSN) as db:
        db.execute("""UPDATE auto_organization_role_grant SET revoked_at=now()
                      WHERE organization_id=%s AND location_id=%s
                        AND user_id=%s AND role_code='INTAKE'
                        AND revoked_at IS NULL""",(org,loc,d["intake"]))
    assert client.get(url).status_code==403
    with psycopg.connect(DSN) as db:
        db.execute("""INSERT INTO auto_organization_role_grant
                     (id,organization_id,location_id,user_id,role_code)
                     VALUES(%s,%s,%s,%s,'INTAKE')""",
                   (uuid4(),org,loc,d["intake"]))
        db.execute("UPDATE auto_vehicle_customer_link SET revoked_at=now() WHERE id=%s",
                   (d["link"],))
    assert client.get(url).status_code==403
    monkeypatch.delenv("ROBOport_SYNTHETIC_AUTO_API")
    assert client.get(url).status_code==404


def test_workspace_direct_service_requires_authenticated_identities():
    with pytest.raises(AutoStockDenied,match="FORBIDDEN"):
        read_order_workspace(None,"",uuid4(),uuid4(),uuid4())
    with pytest.raises(AutoStockDenied,match="FORBIDDEN"):
        read_order_workspace(None,"test",uuid4(),uuid4(),"not uuid")


def test_workspace_redacts_estimate_and_handover_policy_denials(monkeypatch):
    d,org,loc,_,_,orders,client,_=setup(monkeypatch,count=1)
    order=orders[0]
    with psycopg.connect(DSN) as db:
        db.execute("""INSERT INTO auto_organization_role_grant
          (id,organization_id,location_id,user_id,role_code)
          VALUES(%s,%s,%s,%s,'MANAGER')""",(uuid4(),org,loc,d["manager"]))
        db.execute("""INSERT INTO auto_organization_role_grant
          (id,organization_id,location_id,user_id,role_code)
          VALUES(%s,%s,NULL,%s,'ADMIN')""",(uuid4(),org,d["manager"]))
        admin=d["tokens"][d["manager"]]
        for document in ("ESTIMATE","HANDOVER"):
            publish_policy(
                db,admin,org,request_key=uuid4(),role="MANAGER",
                loc=loc,user=None,document=document,cap="READ",
                allowed=False,not_before=None,
            )
    client.cookies.set("rp_session",d["tokens"][d["manager"]])
    path=(f"/api/v1/dev/auto/organizations/{org}/locations/{loc}"
          f"/orders/{order}/workspace")
    response=client.get(path)
    assert response.status_code==200,response.text
    assert response.json()["steps"]["estimate"]=="RESTRICTED"
    assert response.json()["steps"]["technicalDocuments"]=="RESTRICTED"
    assert response.json()["steps"]["intakeAct"]=="MISSING"
    assert response.json()["releaseAllowed"] is False
