"""PG17/HTTPS: initial appointment work selection remains customer-manageable.

The customer may move/cancel BEFORE staff service additions, inspection,
estimates or physical stock events. A move cannot shorten selected service time.
"""
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import psycopg
import pytest

from roboport_api.auto_appointment_service import (
    AppointmentDurationInvalid, create_appointment, reschedule_appointment,
)
from roboport_api.auto_order_service_link import add_synthetic_order_service
from test_pg_auto_customer_booking_http import _setup, _auth, ROOT
from test_pg_auto_booking_services_atomic import _catalog
from test_pg_auto_stock_service import grant
from test_pg_baseline import DSN


def _setup_services(monkeypatch):
    d,org,loc,bay,other,_,http=_setup(monkeypatch)
    with psycopg.connect(DSN) as db:
        other_loc=db.execute(
            "SELECT id FROM auto_service_location WHERE organization_id=%s LIMIT 1",
            (other,),
        ).fetchone()[0]
    first,second,long,foreign=_catalog(org,loc,(other,other_loc))
    return d,org,loc,bay,http,first,second,long


def test_selected_services_are_preserved_during_customer_move_and_cancel(monkeypatch):
    d,org,loc,bay,http,first,second,long=_setup_services(monkeypatch)
    headers=_auth(http,d,d["client_a"])
    start=datetime.now(timezone.utc)+timedelta(days=21)
    url=ROOT+"/appointments"
    payload={
        "organizationId":str(org),"locationId":str(loc),
        "customerLinkId":str(d["link"]),"bayId":str(bay),
        "startsAt":start.isoformat(),
        "endsAt":(start+timedelta(hours=2)).isoformat(),
        "serviceIds":[str(first),str(second),str(long)],
    }
    create_key={**headers,"Idempotency-Key":str(uuid4())}
    created=http.post(url,json=payload,headers=create_key)
    assert created.status_code==201,created.text
    identity=created.json()["appointmentId"]
    order=created.json()["workOrderId"]
    read=http.get(ROOT+"/appointments")
    assert read.status_code==200 and read.headers["cache-control"]=="no-store"
    assert read.json()["items"][0]["canManage"] is True
    assert set(read.json()["items"][0]["serviceIds"])=={
        str(first),str(second),str(long),
    }
    with psycopg.connect(DSN) as db:
        rows=db.execute(
            "SELECT selection_origin FROM auto_work_order_service WHERE work_order_id=%s",
            (order,),
        ).fetchall()
        assert rows==[("BOOKING",)]*3
    move_url=url+"/"+identity+"/reschedule"
    moving={**headers,"Idempotency-Key":str(uuid4())}
    too_short={
        "organizationId":str(org),"bayId":str(bay),
        "startsAt":(start+timedelta(hours=3)).isoformat(),
        "endsAt":(start+timedelta(hours=4)).isoformat(),
    }
    refused=http.post(move_url,json=too_short,headers=moving)
    assert refused.status_code==422,refused.text
    assert refused.json()["code"]=="SERVICES_EXCEED_SLOT"
    with psycopg.connect(DSN) as db:
        assert db.execute(
            "SELECT count(*) FROM auto_appointment_reschedule WHERE appointment_id=%s",
            (identity,),
        ).fetchone()[0]==0
    actual={**too_short,"endsAt":(start+timedelta(hours=5)).isoformat()}
    moved=http.post(move_url,json=actual,headers=moving)
    assert moved.status_code==200,moved.text
    assert http.post(move_url,json=actual,headers=moving).json()==moved.json()
    assert http.post(url,json=payload,headers=create_key).status_code==409
    read=http.get(ROOT+"/appointments")
    assert read.json()["items"][0]["canManage"] is True
    assert set(read.json()["items"][0]["serviceIds"])=={
        str(first),str(second),str(long),
    }
    canceled=http.post(
        url+"/"+identity+"/cancel",
        json={"organizationId":str(org)},
        headers={**headers,"Idempotency-Key":str(uuid4())},
    )
    assert canceled.status_code==200,canceled.text
    assert canceled.json()["status"]=="CANCELED"
    assert http.get(ROOT+"/appointments").json()["items"][0]["canManage"] is False
    with psycopg.connect(DSN) as db:
        assert db.execute(
            "SELECT count(*) FROM auto_work_order_service WHERE work_order_id=%s",
            (order,),
        ).fetchone()[0]==3
        rows=db.execute(
            """SELECT event_type,status FROM notification_outbox
               WHERE aggregate_type='auto_appointment' AND aggregate_id=%s""",
            (UUID(identity),),
        ).fetchall()
        assert {k:v for k,v in rows}=={
            "CREATED":"SUPERSEDED","RESCHEDULED":"SUPERSEDED","CANCELED":"PENDING",
        }


def test_staff_service_addition_blocks_self_manage_and_legacy_rows_are_conservative(monkeypatch):
    d,org,loc,bay,http,first,second,long=_setup_services(monkeypatch)
    token=d["tokens"][d["client_a"]]
    headers=_auth(http,d,d["client_a"])
    start=datetime.now(timezone.utc)+timedelta(days=22)
    url=ROOT+"/appointments"
    created=http.post(url,json={
        "organizationId":str(org),"locationId":str(loc),
        "customerLinkId":str(d["link"]),"bayId":str(bay),
        "startsAt":start.isoformat(),
        "endsAt":(start+timedelta(hours=2)).isoformat(),
        "serviceIds":[str(first)],
    },headers={**headers,"Idempotency-Key":str(uuid4())})
    assert created.status_code==201,created.text
    appointment=created.json()["appointmentId"]
    order=created.json()["workOrderId"]
    assert http.get(ROOT+"/appointments").json()["items"][0]["canManage"] is True
    with psycopg.connect(DSN) as db:
        grant(db,d["intake"],org,loc,"MANAGER")
        outcome=add_synthetic_order_service(
            db,d["tokens"][d["intake"]],organization_id=org,
            location_id=loc,work_order_id=UUID(order),service_id=second,
            synthetic_only=True,
        )
        assert outcome["ok"] and outcome["writes"]==1
        assert db.execute(
            "SELECT selection_origin FROM auto_work_order_service WHERE service_id=%s AND work_order_id=%s",
            (second,UUID(order)),
        ).fetchone()==("STAFF",)
    assert http.get(ROOT+"/appointments").json()["items"][0]["canManage"] is False
    denied=http.post(url+"/"+appointment+"/cancel",json={
        "organizationId":str(org),
    },headers={**headers,"Idempotency-Key":str(uuid4())})
    assert denied.status_code==403
    moving=http.post(url+"/"+appointment+"/reschedule",json={
        "organizationId":str(org),"bayId":str(bay),
        "startsAt":(start+timedelta(hours=3)).isoformat(),
        "endsAt":(start+timedelta(hours=5)).isoformat(),
    },headers={**headers,"Idempotency-Key":str(uuid4())})
    assert moving.status_code==403
    with psycopg.connect(DSN) as db:
        assert db.execute(
            "SELECT count(*) FROM auto_appointment_reschedule WHERE appointment_id=%s",
            (UUID(appointment),),
        ).fetchone()[0]==0
        assert db.execute(
            "SELECT count(*) FROM notification_outbox WHERE aggregate_type='auto_appointment' AND aggregate_id=%s",
            (UUID(appointment),),
        ).fetchone()[0]==1
