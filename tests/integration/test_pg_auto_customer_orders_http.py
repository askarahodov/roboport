"""PG17 contract: customer sees original customer-linked WorkOrders, not new owner's history."""
from datetime import datetime,timedelta,timezone
from uuid import uuid4
import psycopg
from fastapi.testclient import TestClient
from roboport_api.main import app
from roboport_api.auto_appointment_service import (
    create_appointment,reschedule_appointment,cancel_appointment,
)
from roboport_api.auto_work_order_estimate import publish_auto_estimate
from roboport_api.auto_demo_close import simulate_cash,seal_handover_preview
from test_pg_baseline import DSN
from test_pg_auto_order_service_http import scenario as order_scenario
from test_pg_auto_work_order_estimate_http import scenario as estimate_scenario
from test_pg_auto_demo_close_http import ready as finished_scenario


PREFIX="/api/v1/dev/auto/customer/orders"


def client(monkeypatch):
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTO_API","1")
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTH_API","1")
    monkeypatch.setenv("ROBOport_DATABASE_URL",DSN)
    return TestClient(app,base_url="https://127.0.0.1")


def login(http,d,user):
    http.cookies.set("rp_session",d["tokens"][user])


def test_exact_customer_link_prevents_new_owner_from_inheriting_old_work_order(monkeypatch):
    d,org,loc,order,service,_,_=estimate_scenario()
    http=client(monkeypatch)
    assert http.get(PREFIX).status_code==401
    login(http,d,d["manager"])
    assert http.get(PREFIX).status_code==403
    login(http,d,d["client_b"])
    assert http.get(PREFIX).json()["items"]==[]
    assert http.get(PREFIX+"/"+str(order)).status_code==404
    login(http,d,d["client_a"])
    response=http.get(PREFIX)
    assert response.status_code==200,response.text
    assert response.headers["cache-control"]=="no-store"
    assert response.json()["syntheticOnly"] is True
    assert response.json()["releaseAllowed"] is False
    assert [x["id"] for x in response.json()["items"]]==[str(order)]
    listed=response.json()["items"][0]
    assert listed["organizationId"]==str(org)
    assert listed["locationId"]==str(loc)
    assert listed["vehicle"]["vinLast4"] is None
    assert "customerId" not in str(response.json())
    assert "vin" not in listed["vehicle"]
    detail=http.get(PREFIX+"/"+str(order))
    assert detail.status_code==200,detail.text
    assert detail.json()["estimates"]==[]
    assert detail.json()["inspection"] is None
    assert detail.json()["document"] is None
    assert detail.json()["releaseAllowed"] is False
    assert detail.json()["canDecide"] is False
    assert http.get(PREFIX+"/"+str(uuid4())).status_code==404
    assert http.get(PREFIX+"/not-a-uuid").status_code==404
    with psycopg.connect(DSN) as db:
        # Customer B is independently verified for the SAME vehicle. This must
        # never grant access to A's historical appointment or documents.
        db.execute("""UPDATE auto_vehicle_customer_link SET verified_at=now(),verified_by=%s
           WHERE id=%s""",(d["manager"],d["other_link"]))
    login(http,d,d["client_b"])
    own=http.get("/api/v1/dev/auto/vehicles").json()
    assert [v["id"] for v in own["items"]]==[str(d["vehicle"])]
    assert http.get(PREFIX).json()["items"]==[]
    assert http.get(PREFIX+"/"+str(order)).status_code==404
    login(http,d,d["client_a"])
    assert http.get(PREFIX+"/"+str(order)).status_code==200
    with psycopg.connect(DSN) as db:
        db.execute("UPDATE auto_vehicle_customer_link SET revoked_at=now() WHERE id=%s",
                   (d["link"],))
    assert http.get(PREFIX).json()["items"]==[]
    assert http.get(PREFIX+"/"+str(order)).status_code==404
    login(http,d,d["client_b"])
    assert http.get(PREFIX+"/"+str(order)).status_code==404


def test_quote_timeline_consent_document_and_live_revocations(monkeypatch):
    d,org,loc,order,service,sku,lot=finished_scenario(monkeypatch,with_material=False)
    http=client(monkeypatch)
    login(http,d,d["client_a"])
    view=http.get(PREFIX+"/"+str(order))
    assert view.status_code==200,view.text
    payload=view.json()
    assert payload["estimates"][-1]["version"]==1
    assert payload["estimates"][-1]["decision"]=="APPROVED"
    assert payload["canDecide"] is False
    assert payload["workStatus"]=="QC_ACCEPTED"
    assert payload["qcStatus"]=="QC_PASSED"
    assert payload["inspection"]["odometerKm"]==6000
    assert payload["labor"]==[{"serviceId":str(service),
                                "title":"SYNTHETIC INSPECTION","actualMinutes":45}]
    assert payload["document"] is None
    with psycopg.connect(DSN) as db:
        simulate_cash(db,d["tokens"][d["intake"]],org,loc,order,
                      request_key=uuid4(),kind="SIM_PAYMENT",amount_minor=2500)
        seal_handover_preview(db,d["tokens"][d["intake"]],org,loc,order,
                              request_key=uuid4())
    now=http.get(PREFIX+"/"+str(order)).json()
    assert len(now["document"]["sha256"])==64
    assert now["document"]["type"]=="SYNTHETIC_PRE_HANDOVER_PREVIEW"
    assert now["releaseAllowed"] is False
    assert "actorId" not in str(now)
    with psycopg.connect(DSN) as db:
        db.execute("UPDATE account_customer_link SET revoked_at=now() WHERE user_id=%s",
                   (d["client_a"],))
    assert http.get(PREFIX).json()["items"]==[]
    assert http.get(PREFIX+"/"+str(order)).status_code==404


def test_unapproved_quote_and_changed_booking_are_visible_only_to_bound_customer(monkeypatch):
    d,org,loc,bay,order,appt,start,service,foreign,_=order_scenario()
    http=client(monkeypatch)
    login(http,d,d["client_a"])
    initial=http.get(PREFIX+"/"+str(order)).json()
    assert initial["appointment"]["status"]=="SCHEDULED"
    with psycopg.connect(DSN) as db:
        from roboport_api.auto_order_service_link import add_synthetic_order_service
        add_synthetic_order_service(db,d["tokens"][d["intake"]],
            organization_id=org,location_id=loc,work_order_id=order,
            service_id=service,synthetic_only=True)
        publish_auto_estimate(db,d["tokens"][d["intake"]],org,loc,order,
            request_key=uuid4(),expected_version=0,
            prices=[{"serviceId":service,"amountMinor":12500}])
        reschedule_appointment(db,d["tokens"][d["intake"]],org,appt,bay,
            start+timedelta(hours=2),start+timedelta(hours=3))
    changed=http.get(PREFIX+"/"+str(order)).json()
    assert changed["appointment"]["startsAt"]!=initial["appointment"]["startsAt"]
    assert changed["canDecide"] is True
    assert changed["estimates"][-1]["totalMinor"]==12500
    # Exact current-version approval still handled by the existing guarded
    # estimate endpoint, not by trusting the client portal's canDecide flag.
    quote=changed["estimates"][-1]
    consent=(f"/api/v1/dev/auto/organizations/{org}/locations/{loc}/orders/{order}"
             "/estimates/decision")
    http.cookies.set("rp_session",d["tokens"][d["client_a"]])
    response=http.post(consent,json={"revisionId":quote["id"],
                                     "requestKey":str(uuid4()),"decision":"APPROVED"},
             headers={"Origin":"https://127.0.0.1",
                      "X-CSRF-Token":d["csrf"][d["client_a"]]})
    assert response.status_code==201,response.text
    assert http.get(PREFIX+"/"+str(order)).json()["canDecide"] is False
    with psycopg.connect(DSN) as db:
        cancel_appointment(db,d["tokens"][d["intake"]],org,appt)
    after=http.get(PREFIX+"/"+str(order)).json()
    assert after["appointment"]["status"]=="CANCELED"
    assert after["canDecide"] is False
    # Appointment cancellation keeps original customer's history,
    # but never allows a new approval of a canceled booking.


def test_keyset_cursor_bound_to_session_and_revalidates_current_acl(monkeypatch):
    d,org,loc,bay,order,appt,start,service,foreign,_=order_scenario()
    with psycopg.connect(DSN) as db:
        for i in range(1,23):
            create_appointment(db,d["tokens"][d["intake"]],org,loc,bay,d["link"],
                start+timedelta(days=i),start+timedelta(days=i,hours=1))
    http=client(monkeypatch)
    login(http,d,d["client_a"])
    page1=http.get(PREFIX+"?limit=10")
    assert page1.status_code==200,page1.text
    assert len(page1.json()["items"])==10
    cursor=page1.json()["nextCursor"]
    assert cursor and len(cursor)<=256
    page2=http.get(PREFIX,params={"limit":"10","cursor":cursor})
    assert page2.status_code==200,page2.text
    assert len(page2.json()["items"])==10
    assert not set(i["id"] for i in page1.json()["items"]).intersection(
        i["id"] for i in page2.json()["items"])
    assert http.get(PREFIX+"?limit=0").status_code==422
    assert http.get(PREFIX+"?limit=10&limit=20").status_code==422
    assert http.get(PREFIX,params={"cursor":"xyz"}).status_code==422
    assert http.get(PREFIX+"?cursor=abc&cursor=abc").status_code==422
    login(http,d,d["client_b"])
    assert http.get(PREFIX,params={"cursor":cursor}).status_code==422
    login(http,d,d["client_a"])
    with psycopg.connect(DSN) as db:
        db.execute("UPDATE user_role SET revoked_at=now() WHERE user_id=%s AND role_code='CLIENT'",
                   (d["client_a"],))
    assert http.get(PREFIX).status_code==403
    assert http.get(PREFIX+"/"+str(order)).status_code==403
    with psycopg.connect(DSN) as db:
        db.execute("UPDATE user_session SET revoked_at=now() WHERE user_id=%s",
                   (d["client_a"],))
    assert http.get(PREFIX).status_code==401
    assert http.get(PREFIX+"/"+str(order)).status_code==401
    import os
    monkeypatch.delenv("ROBOport_SYNTHETIC_AUTO_API")
    assert http.get(PREFIX).status_code==404
    assert http.get(PREFIX+"/"+str(order)).status_code==404
