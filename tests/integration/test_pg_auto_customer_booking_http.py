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
