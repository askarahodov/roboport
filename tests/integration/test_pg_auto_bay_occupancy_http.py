"""PG17 + HTTPS: manager-only live local-day automotive bay occupancy.

DST, reschedules, cancellations, tenant grants and no customer data.
"""
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo

import psycopg
from fastapi.testclient import TestClient

from roboport_api.main import app
from roboport_api.auto_appointment_service import (
    create_appointment, reschedule_appointment, cancel_appointment,
)
from test_pg_auto_vehicle_claim_http import _scenario
from test_pg_auto_bay_booking import _org_and_bay
from test_pg_baseline import DSN


def _fixture(monkeypatch):
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTO_API","1")
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTH_API","1")
    monkeypatch.setenv("ROBOport_DATABASE_URL",DSN)
    d=_scenario()
    with psycopg.connect(DSN) as db:
        org,loc,bay=_org_and_bay(db)
        other,foreign,_=_org_and_bay(db)
        db.execute(
            """UPDATE auto_vehicle_customer_link SET verified_at=now(),
               verified_by=%s WHERE id=%s""",(d["manager"],d["link"]),
        )
        db.execute(
            "INSERT INTO auto_vehicle_location_binding(vehicle_id,location_id) VALUES(%s,%s)",
            (d["vehicle"],loc),
        )
        db.execute(
            "UPDATE auto_service_location SET timezone_name='Europe/Berlin' WHERE id=%s",
            (loc,),
        )
        for actor,role in ((d["manager"],"MANAGER"),(d["intake"],"INTAKE")):
            db.execute(
                """INSERT INTO auto_organization_role_grant
                   (id,organization_id,location_id,user_id,role_code)
                   VALUES(%s,%s,%s,%s,%s)""",
                (uuid4(),org,loc,actor,role),
            )
    return d,org,loc,bay,other,foreign,TestClient(app,base_url="https://127.0.0.1")


def _url(org,loc):
    return f"/api/v1/dev/auto/organizations/{org}/locations/{loc}/bay-occupancy"


def test_manager_report_tracks_current_booking_across_dst_move_and_cancel(monkeypatch):
    d,org,loc,bay,other,foreign,http=_fixture(monkeypatch)
    zone=ZoneInfo("Europe/Berlin")
    start=datetime(2030,3,31,1,30,tzinfo=zone)
    end=datetime(2030,3,31,3,30,tzinfo=zone)  # actual 3600 seconds
    with psycopg.connect(DSN) as db:
        ident,old,order=create_appointment(
            db,d["tokens"][d["intake"]],org,loc,bay,d["link"],start,end,
        )
    path=_url(org,loc)
    query={"fromDay":"2030-03-31","toDay":"2030-04-01"}
    assert http.get(path,params=query).status_code==403
    http.cookies.set("rp_session",d["tokens"][d["manager"]])
    first=http.get(path,params=query)
    assert first.status_code==200,first.text
    assert first.headers["cache-control"]=="no-store"
    data=first.json()
    assert data["syntheticOnly"] is True
    assert data["basis"]=="CURRENT_BOOKINGS_CALENDAR_DAY"
    assert data["timeZone"]=="Europe/Berlin" and data["bayCount"]==1
    assert data["items"]==[
        {
            "localDay":"2030-03-31","daySeconds":23*3600,
            "activeAppointments":1,"occupiedSeconds":3600,
            "calendarCapacitySeconds":23*3600,
            "occupancyBasisPoints":3600*10000//(23*3600),
        },
        {
            "localDay":"2030-04-01","daySeconds":24*3600,
            "activeAppointments":0,"occupiedSeconds":0,
            "calendarCapacitySeconds":24*3600,"occupancyBasisPoints":0,
        },
    ]
    assert "vin" not in first.text.lower()
    assert "customer" not in first.text.lower()
    assert str(d["vehicle"]) not in first.text
    assert str(ident) not in first.text and str(order) not in first.text
    with psycopg.connect(DSN) as db:
        reschedule_appointment(
            db,d["tokens"][d["intake"]],org,ident,bay,
            datetime(2030,4,1,10,tzinfo=zone),datetime(2030,4,1,11,tzinfo=zone),
        )
    moved=http.get(path,params=query)
    assert moved.status_code==200
    assert [x["occupiedSeconds"] for x in moved.json()["items"]]==[0,3600]
    assert [x["activeAppointments"] for x in moved.json()["items"]]==[0,1]
    with psycopg.connect(DSN) as db:
        assert cancel_appointment(db,d["tokens"][d["intake"]],org,ident)=="CANCELED"
    canceled=http.get(path,params=query)
    assert canceled.status_code==200
    assert [x["occupiedSeconds"] for x in canceled.json()["items"]]==[0,0]
    assert [x["activeAppointments"] for x in canceled.json()["items"]]==[0,0]
    assert http.get(_url(other,foreign),params=query).status_code==403
    assert http.get(_url(org,foreign),params=query).status_code==403

    with psycopg.connect(DSN) as db:
        db.execute(
            """UPDATE auto_organization_role_grant SET revoked_at=now()
               WHERE organization_id=%s AND user_id=%s AND role_code='MANAGER'""",
            (org,d["manager"]),
        )
    assert http.get(path,params=query).status_code==403


def test_report_clips_midnight_booking_and_25_hour_autumn_day(monkeypatch):
    d,org,loc,bay,other,foreign,http=_fixture(monkeypatch)
    zone=ZoneInfo("Europe/Berlin")
    with psycopg.connect(DSN) as db:
        create_appointment(
            db,d["tokens"][d["intake"]],org,loc,bay,d["link"],
            datetime(2030,10,26,23,30,tzinfo=zone),
            datetime(2030,10,27,0,30,tzinfo=zone),
        )
    http.cookies.set("rp_session",d["tokens"][d["manager"]])
    path=_url(org,loc)
    result=http.get(path,params={"fromDay":"2030-10-26","toDay":"2030-10-27"})
    assert result.status_code==200,result.text
    rows=result.json()["items"]
    assert [x["daySeconds"] for x in rows]==[24*3600,25*3600]
    assert [x["occupiedSeconds"] for x in rows]==[1800,1800]
    assert [x["activeAppointments"] for x in rows]==[1,1]
    assert [x["calendarCapacitySeconds"] for x in rows]==[24*3600,25*3600]


def test_bounded_report_validation_and_no_fake_zero_capacity_percent(monkeypatch):
    d,org,loc,bay,other,foreign,http=_fixture(monkeypatch)
    http.cookies.set("rp_session",d["tokens"][d["manager"]])
    path=_url(org,loc)
    for first,last in (
        ("invalid","2030-01-01"),("2030-02-30","2030-03-01"),
        ("2030-01-02","2030-01-01"),("2030-01-01","2030-02-01"),
        ("2030-01-01","9999-12-31"),
    ):
        assert http.get(path,params={"fromDay":first,"toDay":last}).status_code==422
    assert http.get(path.replace(str(org),"no-uuid"),params={
        "fromDay":"2030-01-01","toDay":"2030-01-01",
    }).status_code==422
    with psycopg.connect(DSN) as db:
        db.execute("UPDATE auto_work_bay SET disabled_at=now() WHERE id=%s",(bay,))
    report=http.get(path,params={"fromDay":"2030-01-01","toDay":"2030-01-01"})
    assert report.status_code==200,report.text
    assert report.json()["bayCount"]==0
    assert report.json()["items"][0]["occupancyBasisPoints"] is None
    assert report.json()["items"][0]["calendarCapacitySeconds"]==0
