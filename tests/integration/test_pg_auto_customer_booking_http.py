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
    assert resources.json()["items"]==[{
      "organizationId":str(org),"locationId":str(loc),
      "customerLinkId":str(d["link"]),"bayId":str(bay),
      "vehicle":{"make":"Synthetic","model":"Auto"},
      "timeZone":"Europe/Berlin",
    }] or len(resources.json()["items"])==1
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
    moved=http.post(move_url,json=moved_payload,headers=moving)
    assert moved.status_code==200,moved.text
    assert http.post(move_url,json=moved_payload,headers=moving).json()==moved.json()
    assert http.post(url,json=payload,headers=key).status_code==409
    cancel_url=url+"/"+appointment+"/cancel"
    canceled=http.post(cancel_url,json={"organizationId":str(org)},headers=headers)
    assert canceled.status_code==200,canceled.text
    assert canceled.json()["status"]=="CANCELED"
    assert http.post(cancel_url,json={"organizationId":str(org)},headers=headers).json()["status"]=="ALREADY_CANCELED"
    assert http.post(move_url,json=moved_payload,headers=moving).status_code==409
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
