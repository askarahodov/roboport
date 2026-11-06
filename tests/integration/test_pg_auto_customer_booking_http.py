"""PG17/HTTP: клиент записывает только свой подтверждённый автомобиль, без staff RBAC."""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import psycopg
from fastapi.testclient import TestClient

from roboport_api.main import app
from test_pg_auto_vehicle_claim_http import _scenario
from test_pg_auto_bay_booking import _org_and_bay
from test_pg_baseline import DSN

ROOT="/api/v1/dev/auto/customer/booking"


def _setup(monkeypatch):
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTO_API","1")
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTH_API","1")
    monkeypatch.setenv("ROBOport_DATABASE_URL",DSN)
    d=_scenario()
    with psycopg.connect(DSN) as db:
        org,loc,bay=_org_and_bay(db)
        foreign,other_loc,other_bay=_org_and_bay(db)
        db.execute(
            "UPDATE auto_vehicle_customer_link SET verified_at=now(),verified_by=%s WHERE id=%s",
            (d["manager"],d["link"]),
        )
        db.execute(
            "INSERT INTO auto_vehicle_location_binding(vehicle_id,location_id) VALUES(%s,%s)",
            (d["vehicle"],loc),
        )
        db.execute("UPDATE auto_service_location SET timezone_name='Europe/Berlin' WHERE id=%s",(loc,))
    http=TestClient(app,base_url="https://127.0.0.1")
    return d,org,loc,bay,foreign,other_bay,http


def _auth(http,d,actor):
    http.cookies.set("rp_session",d["tokens"][actor])
    return {"Origin":"https://127.0.0.1","X-CSRF-Token":d["csrf"][actor]}


def test_customer_book_move_cancel_own_scope_and_replays(monkeypatch):
    d,org,loc,bay,other,other_bay,http=_setup(monkeypatch)
    assert http.get(ROOT+"/resources").status_code==401
    _auth(http,d,d["manager"])
    assert http.get(ROOT+"/resources").status_code==403
    _auth(http,d,d["client_b"])
    assert http.get(ROOT+"/resources").json()["items"]==[]
    assert http.get(ROOT+"/appointments").json()["items"]==[]
    headers=_auth(http,d,d["client_a"])
    resources=http.get(ROOT+"/resources")
    assert resources.status_code==200,resources.text
    assert resources.headers["cache-control"]=="no-store"
    assert len(resources.json()["items"])==1
    own_resource=resources.json()["items"][0]
    assert own_resource["organizationId"]==str(org)
    assert own_resource["locationId"]==str(loc)
    assert own_resource["customerLinkId"]==str(d["link"])
    assert own_resource["bayId"]==str(bay)
    assert own_resource["timeZone"]=="Europe/Berlin"
    assert str(d["ca"]) not in resources.text and "vin" not in resources.text.lower()
    day=(datetime.now(timezone.utc)+timedelta(days=20)).date().isoformat()
    slots=http.get(ROOT+"/slots",params={
      "organizationId":str(org),"locationId":str(loc),
      "customerLinkId":str(d["link"]),"bayId":str(bay),"localDay":day,
    })
    assert slots.status_code==200,slots.text
    assert slots.json()["timeZone"]=="Europe/Berlin"
    assert slots.json()["guaranteed"] is False
    assert len(slots.json()["items"])>0
    at=datetime.now(timezone.utc)+timedelta(days=20)
    payload={"organizationId":str(org),"locationId":str(loc),
      "customerLinkId":str(d["link"]),"bayId":str(bay),
      "startsAt":at.isoformat(),"endsAt":(at+timedelta(hours=1)).isoformat()}
    url=ROOT+"/appointments"
    assert http.post(url,json=payload,headers=headers).status_code==422
    key={**headers,"Idempotency-Key":str(uuid4())}
    for invalid in (
        {**payload, "endsAt": at.isoformat()},
        {**payload, "startsAt": at.replace(tzinfo=None).isoformat()},
        {**payload, "endsAt": (at+timedelta(hours=25)).isoformat()},
    ):
        result=http.post(url,json=invalid,headers={
            **headers,"Idempotency-Key":str(uuid4())})
        assert result.status_code==422,result.text
        assert result.json()["code"]=="INVALID_INPUT"
    with psycopg.connect(DSN) as db:
        assert db.execute(
            "SELECT count(*) FROM auto_appointment WHERE organization_id=%s",
            (org,),
        ).fetchone()==(0,)
    created=http.post(url,json=payload,headers=key)
    assert created.status_code==201,created.text
    assert http.post(url,json=payload,headers=key).json()==created.json()
    appointment=created.json()["appointmentId"]
    work=created.json()["workOrderId"]
    wrong=http.post(url,json={**payload,"bayId":str(other_bay)},headers={
      **headers,"Idempotency-Key":str(uuid4())})
    assert wrong.status_code==403
    assert http.post(url,json=payload,headers={
      **headers,"Idempotency-Key":str(uuid4())}).status_code==409
    own=http.get(ROOT+"/appointments")
    assert own.status_code==200,own.text
    assert len(own.json()["items"])==1
    assert own.json()["items"][0]["appointmentId"]==appointment
    assert own.json()["items"][0]["workOrderId"]==work
    assert own.json()["items"][0]["canManage"] is True
    _auth(http,d,d["client_b"])
    assert http.get(ROOT+"/appointments").json()["items"]==[]
    assert http.post(url,json=payload,headers={
      "Origin":"https://127.0.0.1","X-CSRF-Token":d["csrf"][d["client_b"]],
      "Idempotency-Key":str(uuid4())}).status_code==403
    headers=_auth(http,d,d["client_a"])
    moving={**headers,"Idempotency-Key":str(uuid4())}
    move_url=url+"/"+appointment+"/reschedule"
    moved_payload={"organizationId":str(org),"bayId":str(bay),
      "startsAt":(at+timedelta(hours=2)).isoformat(),
      "endsAt":(at+timedelta(hours=3)).isoformat()}
    for invalid_move in (
        {**moved_payload, "endsAt": moved_payload["startsAt"]},
        {**moved_payload, "endsAt": (at+timedelta(hours=28)).isoformat()},
        {**moved_payload, "startsAt": (at+timedelta(hours=2)).replace(tzinfo=None).isoformat()},
    ):
        invalid_result=http.post(move_url,json=invalid_move,headers={
            **headers,"Idempotency-Key":str(uuid4())})
        assert invalid_result.status_code==422,invalid_result.text
        assert invalid_result.json()["code"]=="INVALID_INPUT"
    with psycopg.connect(DSN) as db:
        assert db.execute(
            "SELECT count(*) FROM auto_appointment_reschedule WHERE appointment_id=%s",
            (appointment,),
        ).fetchone()==(0,)
    moved=http.post(move_url,json=moved_payload,headers=moving)
    assert moved.status_code==200,moved.text
    assert http.post(move_url,json=moved_payload,headers=moving).json()==moved.json()
    assert http.post(url,json=payload,headers=key).status_code==409
    cancel_url=url+"/"+appointment+"/cancel"
    cancel_headers={**headers,"Idempotency-Key":str(uuid4())}
    canceled=http.post(cancel_url,json={"organizationId":str(org)},headers=cancel_headers)
    assert canceled.status_code==200,canceled.text
    assert canceled.json()["status"]=="CANCELED"
    assert http.post(cancel_url,json={"organizationId":str(org)},headers=cancel_headers).json()["status"]=="CANCELED"
    assert http.post(cancel_url,json={"organizationId":str(org)},headers=headers).json()["status"]=="ALREADY_CANCELED"
    assert http.post(cancel_url,json={"organizationId":str(org)},headers={**headers,"Idempotency-Key":"invalid"}).status_code==422
    assert http.post(move_url,json=moved_payload,headers=moving).status_code==409
    # A receipt is not an authority grant: revoking the customer proof denies replay.
    with psycopg.connect(DSN) as db:
        db.execute("UPDATE auto_vehicle_customer_link SET revoked_at=now() WHERE id=%s",
                   (d["link"],))
    denied=http.post(cancel_url,json={"organizationId":str(org)},headers=cancel_headers)
    assert denied.status_code==403,denied.text
    with psycopg.connect(DSN) as db:
        assert db.execute(
            "SELECT count(*) FROM auto_work_order WHERE appointment_id=%s",(appointment,)
        ).fetchone()[0]==1
        assert db.execute(
            "SELECT count(*) FROM auto_appointment_reschedule WHERE appointment_id=%s",(appointment,)
        ).fetchone()[0]==1
        assert db.execute(
            """SELECT count(*) FROM notification_outbox
               WHERE aggregate_type='auto_appointment' AND aggregate_id=%s""",
            (appointment,),
        ).fetchone()[0]==3


def test_customer_access_revoked_before_mutation(monkeypatch):
    d,org,loc,bay,other,other_bay,http=_setup(monkeypatch)
    headers=_auth(http,d,d["client_a"])
    at=datetime.now(timezone.utc)+timedelta(days=19)
    payload={"organizationId":str(org),"locationId":str(loc),
             "customerLinkId":str(d["link"]),"bayId":str(bay),
             "startsAt":at.isoformat(),"endsAt":(at+timedelta(hours=1)).isoformat()}
    with psycopg.connect(DSN) as db:
        db.execute("UPDATE auto_vehicle_customer_link SET revoked_at=now() WHERE id=%s",(d["link"],))
    assert http.get(ROOT+"/resources").json()["items"]==[]
    assert http.post(ROOT+"/appointments",json=payload,
        headers={**headers,"Idempotency-Key":str(uuid4())}).status_code==403
    assert http.get(ROOT+"/appointments").json()["items"]==[]
    with psycopg.connect(DSN) as db:
        assert db.execute(
          "SELECT count(*) FROM auto_appointment WHERE organization_id=%s",(org,)
        ).fetchone()[0]==0


def test_customer_cannot_change_appointment_after_inspection(monkeypatch):
    d,org,loc,bay,_,_,http=_setup(monkeypatch)
    headers=_auth(http,d,d["client_a"])
    at=datetime.now(timezone.utc)+timedelta(days=24)
    payload={"organizationId":str(org),"locationId":str(loc),
             "customerLinkId":str(d["link"]),"bayId":str(bay),
             "startsAt":at.isoformat(),"endsAt":(at+timedelta(hours=1)).isoformat()}
    created=http.post(ROOT+"/appointments",json=payload,
                      headers={**headers,"Idempotency-Key":str(uuid4())})
    assert created.status_code==201,created.text
    order=created.json()["workOrderId"]
    appointment=created.json()["appointmentId"]
    with psycopg.connect(DSN) as db:
        db.execute(
            """INSERT INTO auto_work_order_inspection(
                 id,work_order_id,organization_id,location_id,actor_id,
                 odometer_km,fuel_percent,tyres_ok,lights_ok,fluid_leak,exterior_code
               ) VALUES(%s,%s,%s,%s,%s,1000,50,true,true,false,'CLEAR')""",
            (uuid4(),order,org,loc,d["intake"]),
        )
    own=http.get(ROOT+"/appointments")
    assert own.status_code==200,own.text
    assert own.json()["items"][0]["canManage"] is False
    moving={"organizationId":str(org),"bayId":str(bay),
            "startsAt":(at+timedelta(hours=2)).isoformat(),
            "endsAt":(at+timedelta(hours=3)).isoformat()}
    move=http.post(ROOT+"/appointments/"+appointment+"/reschedule",
                   json=moving,headers={**headers,"Idempotency-Key":str(uuid4())})
    assert move.status_code==403,move.text
    cancel=http.post(ROOT+"/appointments/"+appointment+"/cancel",
                     json={"organizationId":str(org)},headers=headers)
    assert cancel.status_code==403,cancel.text
    with psycopg.connect(DSN) as db:
        assert db.execute(
            "SELECT count(*) FROM auto_appointment_reschedule WHERE appointment_id=%s",
            (appointment,),
        ).fetchone()[0]==0
        assert db.execute(
            "SELECT canceled_at IS NULL FROM auto_bay_booking WHERE id=%s",
            (created.json()["bookingId"],),
        ).fetchone()==(True,)


def test_two_verified_customers_cannot_double_book_same_bay(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from roboport_api.auto_appointment_service import create_appointment, AppointmentConflict
    d,org,loc,bay,_,_,http=_setup(monkeypatch)
    with psycopg.connect(DSN) as db:
        db.execute(
            "UPDATE auto_vehicle_customer_link SET verified_at=now(),verified_by=%s WHERE id=%s",
            (d["manager"],d["other_link"]),
        )
    at=datetime.now(timezone.utc)+timedelta(days=25)
    barrier=Barrier(2)
    def attempt(who,proof):
        with psycopg.connect(DSN) as db:
            barrier.wait(timeout=20)
            try:
                create_appointment(
                    db,d["tokens"][who],org,loc,bay,proof,at,
                    at+timedelta(hours=1),request_key=uuid4(),customer_mode=True,
                )
                return "CREATED"
            except AppointmentConflict:
                return "BUSY"
    with ThreadPoolExecutor(max_workers=2) as pool:
        one=pool.submit(attempt,d["client_a"],d["link"])
        two=pool.submit(attempt,d["client_b"],d["other_link"])
        assert sorted([one.result(timeout=30),two.result(timeout=30)])==["BUSY","CREATED"]
    with psycopg.connect(DSN) as db:
        assert db.execute(
            "SELECT count(*) FROM auto_bay_booking WHERE organization_id=%s",
            (org,),
        ).fetchone()[0]==1
        assert db.execute(
            "SELECT count(*) FROM auto_work_order WHERE organization_id=%s",
            (org,),
        ).fetchone()[0]==1


def test_customer_appointments_cursor_more_than_fifty_and_revocation(monkeypatch):
    """52 owned bookings are all reachable, without VIN or cross-account cursor reuse."""
    from roboport_api.auto_appointment_service import create_appointment
    d,org,loc,bay,_,_,http=_setup(monkeypatch)
    at=datetime.now(timezone.utc)+timedelta(days=30)
    with psycopg.connect(DSN) as db:
        for i in range(52):
            create_appointment(db,d["tokens"][d["client_a"]],org,loc,bay,d["link"],
                               at+timedelta(hours=2*i),
                               at+timedelta(hours=2*i+1),
                               request_key=uuid4(),customer_mode=True)
    _auth(http,d,d["client_a"])
    url=ROOT+"/appointments"
    first_page=http.get(url)
    assert first_page.status_code==200,first_page.text
    first=first_page.json()
    assert len(first["items"])==50 and first["nextCursor"]
    assert first_page.headers["cache-control"]=="no-store"
    assert "vin" not in first_page.text.lower()
    ids=[row["appointmentId"] for row in first["items"]]
    assert len(set(ids))==50
    second_page=http.get(url,params={"cursor":first["nextCursor"]})
    assert second_page.status_code==200,second_page.text
    second=second_page.json()
    assert len(second["items"])==2 and second["nextCursor"] is None
    ids.extend(row["appointmentId"] for row in second["items"])
    assert len(set(ids))==52
    # Cursor is an opaque session-bound token, never a reusable cross-user offset.
    _auth(http,d,d["client_b"])
    foreign=http.get(url,params={"cursor":first["nextCursor"]})
    assert foreign.status_code==422 and foreign.json()["code"]=="INVALID_CURSOR"
    assert http.get(url).json()["items"]==[]
    _auth(http,d,d["client_a"])
    for invalid in ("invalid***", "x"*257, first["nextCursor"]+"A"):
        bad=http.get(url,params={"cursor":invalid})
        assert bad.status_code==422,bad.text
        assert bad.json()["code"]=="INVALID_CURSOR"
    # Revocation after page one removes all subsequent rows immediately.
    with psycopg.connect(DSN) as db:
        db.execute("UPDATE auto_vehicle_customer_link SET revoked_at=now() WHERE id=%s",
                   (d["link"],))
    revoked=http.get(url,params={"cursor":first["nextCursor"]})
    assert revoked.status_code==200,revoked.text
    assert revoked.json()["items"]==[]
    assert revoked.json()["nextCursor"] is None


def test_customer_resource_pages_cover_all_bays_and_recheck_access(monkeypatch):
    """52 vehicle/bay choices, no omissions, cross-session reuse or post-revoke leak."""
    d,org,loc,bay,_,_,http=_setup(monkeypatch)
    with psycopg.connect(DSN) as db:
        for i in range(51):
            db.execute("""INSERT INTO auto_work_bay
                (id,organization_id,location_id,code)
                VALUES(%s,%s,%s,%s)""",(uuid4(),org,loc,f"EXTRA-{i:03d}"))
    _auth(http,d,d["client_a"])
    root=ROOT+"/resources"
    first_response=http.get(root)
    assert first_response.status_code==200,first_response.text
    assert first_response.headers["cache-control"]=="no-store"
    first=first_response.json()
    assert first["syntheticOnly"] is True
    assert len(first["items"])==50 and first["nextCursor"]
    assert "vin" not in first_response.text.lower()
    second_response=http.get(root,params={"cursor":first["nextCursor"]})
    assert second_response.status_code==200,second_response.text
    second=second_response.json()
    assert len(second["items"])==2 and second["nextCursor"] is None
    ids=[r["bayId"] for r in first["items"]+second["items"]]
    assert len(ids)==len(set(ids))==52
    assert all(r["locationId"]==str(loc) and r["customerLinkId"]==str(d["link"])
               for r in first["items"]+second["items"])
    _auth(http,d,d["client_b"])
    foreign=http.get(root,params={"cursor":first["nextCursor"]})
    assert foreign.status_code==422,foreign.text
    assert foreign.json()["code"]=="INVALID_CURSOR"
    assert http.get(root).json()["items"]==[]
    _auth(http,d,d["client_a"])
    for invalid in ("invalid***","x"*257,first["nextCursor"]+"A"):
        response=http.get(root,params={"cursor":invalid})
        assert response.status_code==422,response.text
        assert response.json()["code"]=="INVALID_CURSOR"
    with psycopg.connect(DSN) as db:
        db.execute("UPDATE auto_vehicle_customer_link SET revoked_at=now() WHERE id=%s",
                   (d["link"],))
    revoked=http.get(root,params={"cursor":first["nextCursor"]})
    assert revoked.status_code==200,revoked.text
    assert revoked.json()["items"]==[]
    assert revoked.json()["nextCursor"] is None
