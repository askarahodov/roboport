"""PG17/HTTPS: complete staff order register across pagination, scope and revocation."""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import psycopg
from fastapi.testclient import TestClient
from roboport_api.main import app
from test_pg_baseline import DSN
from test_pg_auto_vehicle_claim_http import _scenario
from test_pg_auto_bay_booking import _org_and_bay
from roboport_api.auto_document_access import publish_policy


def setup(monkeypatch, count=26):
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTO_API","1")
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTH_API","1")
    monkeypatch.setenv("ROBOport_DATABASE_URL",DSN)
    d=_scenario()
    identifiers=[]
    with psycopg.connect(DSN) as db:
        org,loc,bay=_org_and_bay(db)
        foreign,foreign_loc,_=_org_and_bay(db)
        db.execute(
            "UPDATE auto_vehicle_customer_link SET verified_at=now(),verified_by=%s WHERE id=%s",
            (d["manager"],d["link"]),
        )
        db.execute("""INSERT INTO auto_vehicle_location_binding(vehicle_id,location_id)
                      VALUES(%s,%s)""",(d["vehicle"],loc))
        db.execute("""INSERT INTO auto_organization_role_grant(
                        id,organization_id,location_id,user_id,role_code)
                      VALUES(%s,%s,%s,%s,'INTAKE')""",
                   (uuid4(),org,loc,d["intake"]))
        start=datetime(2035,2,1,9,tzinfo=timezone.utc)
        for i in range(count):
            begin=start+timedelta(minutes=i*20)
            booking,appointment,order=uuid4(),uuid4(),uuid4()
            db.execute("""INSERT INTO auto_bay_booking
               (id,organization_id,location_id,bay_id,starts_at,ends_at)
               VALUES(%s,%s,%s,%s,%s,%s)""",
               (booking,org,loc,bay,begin,begin+timedelta(minutes=10)))
            db.execute("""INSERT INTO auto_appointment
               (id,booking_id,organization_id,location_id,vehicle_id,customer_link_id)
               VALUES(%s,%s,%s,%s,%s,%s)""",
               (appointment,booking,org,loc,d["vehicle"],d["link"]))
            db.execute("""INSERT INTO auto_work_order
               (id,appointment_id,organization_id,location_id)
               VALUES(%s,%s,%s,%s)""",(order,appointment,org,loc))
            identifiers.append(order)
    client=TestClient(app,base_url="https://127.0.0.1")
    url=f"/api/v1/dev/auto/organizations/{org}/locations/{loc}/orders"
    return d,org,loc,foreign,foreign_loc,identifiers,client,url


def test_register_26_orders_signed_pagination_filters_and_no_pii(monkeypatch):
    d,org,loc,foreign,other_loc,orders,http,url=setup(monkeypatch)
    assert http.get(url).status_code==403
    http.cookies.set("rp_session",d["tokens"][d["intake"]])
    first=http.get(url)
    assert first.status_code==200,first.text
    assert first.headers["cache-control"]=="no-store"
    value=first.json()
    assert value["syntheticOnly"] is True
    assert value["releaseAllowed"] is False
    assert len(value["items"])==25
    assert value["nextCursor"]
    assert set(x["stage"] for x in value["items"])=={"DRAFT"}
    assert all(x["workOrderId"] in {str(ident) for ident in orders}
               for x in value["items"])
    for forbidden in (str(d["vehicle"]),str(d["link"]),str(d["manager"]),
                      "customerId","vehicleId","customerLinkId"):
        assert forbidden not in first.text
    second=http.get(url,params={"cursor":value["nextCursor"]})
    assert second.status_code==200,second.text
    assert len(second.json()["items"])==1
    assert second.json()["nextCursor"] is None
    seen={x["workOrderId"] for x in value["items"]+second.json()["items"]}
    assert seen=={str(ident) for ident in orders}
    assert http.get(url,params={"stage":"READY"}).json()["items"]==[]
    assert len(http.get(url,params={"stage":"DRAFT"}).json()["items"])==25
    exact=http.get(url,params={"orderId":str(orders[-1])})
    assert exact.status_code==200
    assert [x["workOrderId"] for x in exact.json()["items"]]==[str(orders[-1])]
    assert http.get(url,params={"orderId":str(uuid4())}).json()["items"]==[]
    # A signed cursor cannot be replayed against a different filter,
    # branch, or session, and an attacker cannot inject raw timestamps.
    assert http.get(url,params={"cursor":value["nextCursor"],"stage":"DRAFT"}).status_code==422
    assert http.get(url,params={"cursor":"1"}).status_code==422
    assert http.get(url,params={"stage":"INVALID"}).status_code==422
    assert http.get(url,params={"orderId":"bad"}).status_code==422
    assert http.get(url,params={"stage":["ALL","DRAFT"]}).status_code==422
    assert http.get(url,params={"cursor":["a","b"]}).status_code==422
    wrong=url.replace(str(org),str(foreign)).replace(str(loc),str(other_loc))
    assert http.get(wrong).status_code==403
    wrong_loc=url.replace(str(loc),str(other_loc))
    assert http.get(wrong_loc).status_code==403
    http.cookies.clear()
    assert http.get(url).status_code==403


def test_register_rechecks_scope_after_role_or_proof_revocation(monkeypatch):
    d,org,loc,foreign,foreign_loc,orders,http,url=setup(monkeypatch,2)
    http.cookies.set("rp_session",d["tokens"][d["intake"]])
    assert len(http.get(url).json()["items"])==2
    with psycopg.connect(DSN) as db:
        db.execute("""UPDATE auto_organization_role_grant SET revoked_at=now()
           WHERE organization_id=%s AND location_id=%s AND user_id=%s
             AND role_code='INTAKE' AND revoked_at IS NULL""",
           (org,loc,d["intake"]))
    assert http.get(url).status_code==403
    with psycopg.connect(DSN) as db:
        db.execute("""INSERT INTO auto_organization_role_grant(
            id,organization_id,location_id,user_id,role_code)
            VALUES(%s,%s,%s,%s,'INTAKE')""",
            (uuid4(),org,loc,d["intake"]))
        db.execute("""UPDATE auto_vehicle_customer_link SET revoked_at=now()
                      WHERE id=%s""",(d["link"],))
    allowed=http.get(url)
    assert allowed.status_code==200,allowed.text
    assert allowed.json()["items"]==[]


def _admin_policy(d,org,loc,*,allowed,not_before=None):
    with psycopg.connect(DSN) as db:
        db.execute("""INSERT INTO auto_organization_role_grant
          (id,organization_id,location_id,user_id,role_code)
          VALUES(%s,%s,NULL,%s,'ADMIN')""",(uuid4(),org,d["manager"]))
        return publish_policy(
            db,d["tokens"][d["manager"]],org,
            request_key=uuid4(),role="INTAKE",loc=loc,user=None,
            document="INTAKE_ACT",cap="READ",allowed=allowed,
            not_before=not_before,
        )


def test_register_enforces_live_document_deny_policy(monkeypatch):
    d,org,loc,foreign,foreign_loc,orders,http,url=setup(monkeypatch,2)
    http.cookies.set("rp_session",d["tokens"][d["intake"]])
    assert len(http.get(url).json()["items"])==2
    _admin_policy(d,org,loc,allowed=False)
    # A valid organization/branch staff grant is not enough to bypass a
    # newer document-level DENY; list and export share this service gate.
    assert http.get(url).status_code==403
    assert http.get(url.replace("/orders","/orders/export")).status_code==403


def test_register_applies_history_cutoff_before_pagination(monkeypatch):
    d,org,loc,foreign,foreign_loc,orders,http,url=setup(monkeypatch,3)
    http.cookies.set("rp_session",d["tokens"][d["intake"]])
    assert len(http.get(url).json()["items"])==3
    cutoff=datetime.now(timezone.utc)+timedelta(days=1)
    _admin_policy(d,org,loc,allowed=True,not_before=cutoff)
    response=http.get(url)
    assert response.status_code==200,response.text
    assert response.json()["items"]==[]
    assert response.json().get("nextCursor") is None
    # The exact-order locator is subject to the same cutoff as the list.
    assert http.get(url,params={"orderId":str(orders[0])}).json()["items"]==[]
