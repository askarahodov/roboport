"""PG17 + real HTTPS assignment queue, live scope, accept/decline and revocation.

No fake role callbacks: session cookies, scoped grants, real booking and
immutable assignment event log are exercised end-to-end.
"""
from datetime import datetime, timedelta, timezone
from uuid import uuid4
import psycopg
from fastapi.testclient import TestClient

from roboport_api.main import app
from roboport_api.auto_appointment_service import cancel_appointment
from test_pg_baseline import DSN
from test_pg_auto_order_service_http import scenario as order_scenario
from test_pg_auto_stock_service import grant
from test_pg_auto_work_order_assignment_http import auth


def fixture(monkeypatch):
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTO_API", "1")
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTH_API", "1")
    monkeypatch.setenv("ROBOport_DATABASE_URL", DSN)
    d, org, loc, _, order, appointment, _, _, _, _ = order_scenario()
    starts = datetime.now(timezone.utc) + timedelta(hours=3)
    ends = starts + timedelta(hours=1)
    with psycopg.connect(DSN) as db:
        grant_id = grant(db, d["manager"], org, loc, "MECHANIC")
        grant(db, d["other"], org, loc, "MECHANIC")
        shift = uuid4()
        db.execute("""INSERT INTO auto_staff_shift
          (id,organization_id,location_id,user_id,starts_at,ends_at)
          VALUES(%s,%s,%s,%s,%s,%s)""",(
            shift,org,loc,d["manager"],starts-timedelta(minutes=5),
            ends+timedelta(minutes=5)))
    client = TestClient(app, base_url="https://127.0.0.1")
    root = f"/api/v1/dev/auto/organizations/{org}/locations/{loc}"
    body = {"mechanicId": str(d["manager"]), "startsAt": starts.isoformat(),
            "endsAt": ends.isoformat(), "requestKey": str(uuid4())}
    return d, org, loc, order, appointment, grant_id, shift, client, root, body


def offer(client,d,org,loc,order,body):
    manager = auth(client,d,d["intake"])
    return client.post(
        f"/api/v1/dev/auto/organizations/{org}/locations/{loc}"
        f"/orders/{order}/assignments", json=body, headers=manager)


def test_manager_offer_mechanic_accept_decline_and_branch_restrictions(monkeypatch):
    d,org,loc,order,appointment,grant_id,shift,client,root,body=fixture(monkeypatch)
    endpoint = root+"/assignments"
    assert client.get(endpoint).status_code==403
    offered=offer(client,d,org,loc,order,body)
    assert offered.status_code==201,offered.text
    ident=offered.json()["assignmentId"]
    assert offer(client,d,org,loc,order,body).status_code==200
    manager_queue=client.get(endpoint)
    assert manager_queue.status_code==200,manager_queue.text
    assert manager_queue.headers["cache-control"]=="no-store"
    assert manager_queue.json()["syntheticOnly"] is True
    assert manager_queue.json()["releaseAllowed"] is False
    assert manager_queue.json()["canOffer"] is True
    assert manager_queue.json()["items"][0]["workOrderId"]==str(order)
    assert manager_queue.json()["items"][0]["canRespond"] is False
    assert len(manager_queue.json()["items"])==1

    auth(client,d,d["client_a"])
    assert client.get(endpoint).status_code==403
    mechanic=auth(client,d,d["manager"])
    own=client.get(endpoint)
    assert own.status_code==200,own.text
    assert own.json()["canOffer"] is False
    assert own.json()["items"][0]["canRespond"] is True
    assert client.get(endpoint.replace(str(loc),str(uuid4()))).status_code==403
    other=auth(client,d,d["other"])
    own_other=client.get(endpoint)
    assert own_other.status_code==200
    assert own_other.json()["items"]==[]
    accept=endpoint+"/"+ident+"/accept"
    assert client.post(accept,json={"requestKey":str(uuid4())},headers=other).status_code==403
    mechanic=auth(client,d,d["manager"])
    key=str(uuid4())
    accepted=client.post(accept,json={"requestKey":key},headers=mechanic)
    assert accepted.status_code==201,accepted.text
    assert accepted.json()["status"]=="ACCEPTED"
    assert client.post(accept,json={"requestKey":key},headers=mechanic).status_code==200
    assert client.post(accept,json={"requestKey":str(uuid4())},headers=mechanic).status_code==409
    assert client.get(endpoint).json()["items"][0]["canRespond"] is False
    with psycopg.connect(DSN) as db:
        assert db.execute("""SELECT action FROM auto_work_order_assignment_event
            WHERE assignment_id=%s ORDER BY created_at,id""",(ident,)).fetchall()==[
                ("OFFERED",),("ACCEPTED",),
            ]


def test_revoked_owner_proof_hides_offer_and_blocks_direct_accept(monkeypatch):
    d,org,loc,order,appointment,grant_id,shift,client,root,body=fixture(monkeypatch)
    offered=offer(client,d,org,loc,order,body)
    assert offered.status_code==201,offered.text
    ident=offered.json()["assignmentId"]
    with psycopg.connect(DSN) as db:
        db.execute("UPDATE auto_vehicle_customer_link SET revoked_at=now() WHERE id=%s",
                   (d["link"],))
    endpoint=root+"/assignments"
    auth(client,d,d["intake"])
    after=client.get(endpoint)
    assert after.status_code==200,after.text
    assert after.json()["items"]==[]
    mechanic=auth(client,d,d["manager"])
    assert client.get(endpoint).json()["items"]==[]
    assert client.post(endpoint+"/"+ident+"/accept",headers=mechanic,
                       json={"requestKey":str(uuid4())}).status_code==403
    # Decline is still safe: no work may be accepted and no execution starts.
    denied=client.post(endpoint+"/"+ident+"/decline",headers=mechanic,
                       json={"requestKey":str(uuid4())})
    assert denied.status_code==201,denied.text
    with psycopg.connect(DSN) as db:
        assert db.execute("SELECT status FROM auto_work_order_assignment WHERE id=%s",
                          (ident,)).fetchone()==("DECLINED",)


def test_cancel_current_booking_disappears_and_refuses_accept(monkeypatch):
    d,org,loc,order,appointment,grant_id,shift,client,root,body=fixture(monkeypatch)
    offered=offer(client,d,org,loc,order,body)
    assert offered.status_code==201,offered.text
    ident=offered.json()["assignmentId"]
    with psycopg.connect(DSN) as db:
        assert cancel_appointment(db,d["tokens"][d["intake"]],org,appointment,
                                  request_key=uuid4())=="CANCELED"
    auth(client,d,d["intake"])
    assert client.get(root+"/assignments").json()["items"]==[]
    mechanic=auth(client,d,d["manager"])
    assert client.post(root+"/assignments/"+ident+"/accept",headers=mechanic,
                       json={"requestKey":str(uuid4())}).status_code==403


def test_revoked_shift_and_grant_never_show_or_accept_stale_offer(monkeypatch):
    d,org,loc,order,appointment,grant_id,shift,client,root,body=fixture(monkeypatch)
    offered=offer(client,d,org,loc,order,body)
    assert offered.status_code==201,offered.text
    ident=offered.json()["assignmentId"]
    with psycopg.connect(DSN) as db:
        db.execute("UPDATE auto_staff_shift SET canceled_at=now() WHERE id=%s",
                   (shift,))
    auth(client,d,d["intake"])
    assert client.get(root+"/assignments").json()["items"]==[]
    mechanic=auth(client,d,d["manager"])
    assert client.post(root+"/assignments/"+ident+"/accept",headers=mechanic,
                       json={"requestKey":str(uuid4())}).status_code==403
    with psycopg.connect(DSN) as db:
        db.execute("UPDATE auto_organization_role_grant SET revoked_at=now() WHERE id=%s",
                   (grant_id,))
    assert client.get(root+"/assignments").status_code==403
    assert client.post(root+"/assignments/"+ident+"/decline",headers=mechanic,
                       json={"requestKey":str(uuid4())}).status_code==403
    auth(client,d,d["intake"])
    assert client.get(root+"/assignments").json()["items"]==[]
