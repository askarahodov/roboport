"""PG17/HTTP: scoped opening hours, DST, closed dates and atomic booking guards."""
from datetime import datetime, timedelta, timezone, date, time
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import psycopg
import pytest

from roboport_api.auto_opening_hours import set_opening
from test_pg_auto_customer_booking_http import _setup, _auth, ROOT
from test_pg_auto_stock_service import grant
from test_pg_baseline import DSN

ZONE=ZoneInfo("Europe/Berlin")


def _ready(monkeypatch):
    d,org,loc,bay,foreign,_,http=_setup(monkeypatch)
    with psycopg.connect(DSN) as db:
        grant(db,d["manager"],org,loc,"MANAGER")
        grant(db,d["intake"],org,loc,"INTAKE")
    base=f"/api/v1/dev/auto/organizations/{org}/locations/{loc}/opening-hours"
    return d,org,loc,bay,foreign,http,base


def _weekly(start=540,end=1020):
    return [{"weekday":i,"opensMinute":start,"closesMinute":end}
            for i in range(1,8)]


def _local(day,hour=10,minute=0):
    return datetime(day.year,day.month,day.day,hour,minute,tzinfo=ZONE)


def _day_ahead(n=28):
    return (datetime.now(ZONE)+timedelta(days=n)).date()


def test_manager_configures_branch_and_both_slot_flows_enforce_it(monkeypatch):
    d,org,loc,bay,foreign,http,path=_ready(monkeypatch)
    assert http.get(path).status_code==403
    _auth(http,d,d["client_a"])
    assert http.get(path).status_code==403
    staff=_auth(http,d,d["intake"])
    unconfigured=http.get(path)
    assert unconfigured.status_code==200,unconfigured.text
    assert unconfigured.json()["configured"] is False
    assert unconfigured.json()["version"]==0
    assert unconfigured.json()["timeZone"]=="Europe/Berlin"
    assert http.post(path,json={
        "expectedVersion":0,"windows":_weekly(),"closedDates":[],
    },headers=staff).status_code==403

    manager=_auth(http,d,d["manager"])
    response=http.post(path,json={
        "expectedVersion":0,"windows":_weekly(),"closedDates":[],
    },headers=manager)
    assert response.status_code==200,response.text
    assert response.json()["version"]==1
    assert response.json()["configured"] is True
    assert response.headers["cache-control"]=="no-store"
    with psycopg.connect(DSN) as db:
        assert db.execute(
            "SELECT count(*) FROM auto_location_opening_history WHERE location_id=%s",
            (loc,),
        ).fetchone()[0]==1
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute(
                    "UPDATE auto_location_opening_history SET version=3 WHERE location_id=%s",
                    (loc,),
                )
    day=_day_ahead()
    client_headers=_auth(http,d,d["client_a"])
    query={"organizationId":str(org),"locationId":str(loc),
           "customerLinkId":str(d["link"]),"bayId":str(bay),
           "localDay":day.isoformat()}
    slots=http.get(ROOT+"/slots",params=query)
    assert slots.status_code==200,slots.text
    assert slots.json()["openingHoursConfigured"] is True
    assert slots.json()["items"]
    for slot in slots.json()["items"]:
        begin=datetime.fromisoformat(slot["startsAt"]).astimezone(ZONE)
        end=datetime.fromisoformat(slot["endsAt"]).astimezone(ZONE)
        assert begin.date()==day
        assert 9 <= begin.hour < 17
        assert end.hour <=17 and (end.hour<17 or end.minute==0)
    _auth(http,d,d["intake"])
    staff_slots=http.get(
        f"/api/v1/dev/auto/organizations/{org}/slot-candidates",
        params={"locationId":str(loc),"bayId":str(bay),
                "localDay":day.isoformat()},
    )
    assert staff_slots.status_code==200,staff_slots.text
    assert staff_slots.json()["openingHoursConfigured"] is True
    assert staff_slots.json()["items"]==slots.json()["items"]
    # Config does not leak across tenants or grant branches.
    assert http.get(path.replace(str(loc),str(uuid4()))).status_code==403


def test_outside_hours_rejects_staff_client_reschedule_and_direct_sql(monkeypatch):
    d,org,loc,bay,foreign,http,path=_ready(monkeypatch)
    manager=_auth(http,d,d["manager"])
    configured=http.post(path,json={
        "expectedVersion":0,"windows":_weekly(),"closedDates":[],
    },headers=manager)
    assert configured.status_code==200,configured.text
    day=_day_ahead(30)
    outside=_local(day,18)
    inside=_local(day,10)
    create=f"/api/v1/dev/auto/organizations/{org}/appointments"
    payload={"locationId":str(loc),"bayId":str(bay),
             "customerLinkId":str(d["link"]),
             "startsAt":outside.isoformat(),
             "endsAt":(outside+timedelta(hours=1)).isoformat()}
    staff=_auth(http,d,d["intake"])
    denied=http.post(create,json=payload,
                     headers={**staff,"Idempotency-Key":str(uuid4())})
    assert denied.status_code==409,denied.text
    assert denied.json()["code"]=="LOCATION_CLOSED"
    # Direct SQL cannot skip the opening schedule when bypassing the API.
    with psycopg.connect(DSN) as db:
        with pytest.raises(psycopg.errors.RaiseException,match="outside opening hours"):
            with db.transaction():
                db.execute(
                    """INSERT INTO auto_bay_booking
                       (id,organization_id,location_id,bay_id,starts_at,ends_at)
                       VALUES(%s,%s,%s,%s,%s,%s)""",
                    (uuid4(),org,loc,bay,outside,outside+timedelta(hours=1)),
                )
    customer=_auth(http,d,d["client_a"])
    customer_denied=http.post(ROOT+"/appointments",json={
        **payload,"organizationId":str(org),
    },headers={**customer,"Idempotency-Key":str(uuid4())})
    assert customer_denied.status_code==409,customer_denied.text
    assert customer_denied.json()["code"]=="LOCATION_CLOSED"
    payload.update(startsAt=inside.isoformat(),
                   endsAt=(inside+timedelta(hours=1)).isoformat())
    staff=_auth(http,d,d["intake"])
    created=http.post(create,json=payload,
                      headers={**staff,"Idempotency-Key":str(uuid4())})
    assert created.status_code==201,created.text
    appointment=created.json()["appointmentId"]
    booking=created.json()["bookingId"]
    move_url=create+"/"+appointment+"/reschedule"
    move=http.post(move_url,json={
        "bayId":str(bay),"startsAt":outside.isoformat(),
        "endsAt":(outside+timedelta(hours=1)).isoformat(),
    },headers={**staff,"Idempotency-Key":str(uuid4())})
    assert move.status_code==409,move.text
    assert move.json()["code"]=="LOCATION_CLOSED"
    with psycopg.connect(DSN) as db:
        assert db.execute(
            "SELECT canceled_at IS NULL FROM auto_bay_booking WHERE id=%s",
            (booking,),
        ).fetchone()==(True,)
        assert db.execute(
            "SELECT count(*) FROM auto_appointment_reschedule WHERE appointment_id=%s",
            (appointment,),
        ).fetchone()[0]==0
        assert db.execute(
            "SELECT count(*) FROM auto_appointment WHERE organization_id=%s",
            (org,),
        ).fetchone()[0]==1
        assert db.execute(
            """SELECT count(*) FROM notification_outbox
               WHERE aggregate_type='auto_appointment' AND aggregate_id=%s""",
            (appointment,),
        ).fetchone()[0]==1


def test_policy_update_is_optimistic_audited_and_cannot_strand_future_bookings(monkeypatch):
    d,org,loc,bay,foreign,http,path=_ready(monkeypatch)
    manager=_auth(http,d,d["manager"])
    initial=http.post(path,json={
        "expectedVersion":0,"windows":_weekly(),"closedDates":[],
    },headers=manager)
    assert initial.status_code==200,initial.text
    day=_day_ahead(33)
    start=_local(day,11)
    staff=_auth(http,d,d["intake"])
    created=http.post(f"/api/v1/dev/auto/organizations/{org}/appointments",
        json={"locationId":str(loc),"bayId":str(bay),
              "customerLinkId":str(d["link"]),
              "startsAt":start.isoformat(),
              "endsAt":(start+timedelta(hours=1)).isoformat()},
        headers={**staff,"Idempotency-Key":str(uuid4())},
    )
    assert created.status_code==201,created.text
    manager=_auth(http,d,d["manager"])
    lost=http.post(path,json={
        "expectedVersion":0,"windows":_weekly(),"closedDates":[],
    },headers=manager)
    assert lost.status_code==409,lost.text
    assert lost.json()["code"]=="OPENING_POLICY_CONFLICT"
    conflicting=http.post(path,json={
        "expectedVersion":1,"windows":_weekly(),"closedDates":[day.isoformat()],
    },headers=manager)
    assert conflicting.status_code==409,conflicting.text
    assert conflicting.json()["code"]=="OPENING_POLICY_CONFLICT"
    closed=_day_ahead(35)
    updated=http.post(path,json={
        "expectedVersion":1,"windows":_weekly(),
        "closedDates":[closed.isoformat()],
    },headers=manager)
    assert updated.status_code==200,updated.text
    assert updated.json()["version"]==2
    assert http.get(path).json()["closedDates"]==[closed.isoformat()]
    with psycopg.connect(DSN) as db:
        assert db.execute(
            "SELECT array_agg(version ORDER BY version) FROM auto_location_opening_history WHERE location_id=%s",
            (loc,),
        ).fetchone()[0]==[1,2]
    _auth(http,d,d["client_a"])
    slots=http.get(ROOT+"/slots",params={
        "organizationId":str(org),"locationId":str(loc),
        "customerLinkId":str(d["link"]),"bayId":str(bay),
        "localDay":closed.isoformat(),
    })
    assert slots.status_code==200,slots.text
    assert slots.json()["items"]==[]


def test_invalid_windows_dates_and_cross_tenant_scope(monkeypatch):
    d,org,loc,bay,foreign,http,path=_ready(monkeypatch)
    manager=_auth(http,d,d["manager"])
    good={"expectedVersion":0,"windows":_weekly(),"closedDates":[]}
    samples=[
        {**good,"windows":[{"weekday":1,"opensMinute":900,"closesMinute":800}]},
        {**good,"windows":[{"weekday":1,"opensMinute":540,"closesMinute":600},
                           {"weekday":1,"opensMinute":590,"closesMinute":700}]},
        {**good,"windows":[{"weekday":0,"opensMinute":540,"closesMinute":600}]},
        {**good,"windows":[{"weekday":1,"opensMinute":False,"closesMinute":600}]},
        {**good,"windows":[{"weekday":1,"opensMinute":540,"closesMinute":1441}]},
        {**good,"closedDates":["2030-02-30"]},
        {**good,"closedDates":["2030-01-01"]*2},
        {**good,"expectedVersion":-1},
    ]
    for body in samples:
        denied=http.post(path,json=body,headers=manager)
        assert denied.status_code==422,(body,denied.text)
    assert http.get(path).json()["configured"] is False
    assert http.post(path.replace(str(loc),str(uuid4())),
                     json=good,headers=manager).status_code==403
    assert http.get(path.replace(str(org),str(foreign))).status_code==403


def test_iana_spring_autumn_days_keep_absolute_interval_with_local_weekly_rules(monkeypatch):
    d,org,loc,bay,foreign,http,path=_ready(monkeypatch)
    manager=_auth(http,d,d["manager"])
    updated=http.post(path,json={"expectedVersion":0,
        "windows":[{"weekday":7,"opensMinute":60,"closesMinute":240}],
        "closedDates":[]},headers=manager)
    assert updated.status_code==200,updated.text
    _auth(http,d,d["client_a"])
    for day in ("2030-03-31","2030-10-27"):
        slots=http.get(ROOT+"/slots",params={
            "organizationId":str(org),"locationId":str(loc),
            "customerLinkId":str(d["link"]),"bayId":str(bay),"localDay":day,
        })
        assert slots.status_code==200,slots.text
        assert slots.json()["openingHoursConfigured"] is True
        seen_hours=[]
        for slot in slots.json()["items"]:
            start=datetime.fromisoformat(slot["startsAt"])
            end=datetime.fromisoformat(slot["endsAt"])
            assert end-start==timedelta(hours=1)
            local_start=start.astimezone(ZONE)
            local_end=end.astimezone(ZONE)
            assert local_start.date().isoformat()==day
            assert 1 <= local_start.hour < 4
            assert local_end.hour <=4
            seen_hours.append((local_start.hour,local_start.utcoffset()))
        assert seen_hours
        repeated=[offset for hour,offset in seen_hours if hour==2]
        if day=="2030-03-31":
            assert not repeated
        else:
            assert timedelta(hours=1) in repeated
            assert timedelta(hours=2) in repeated



def test_dst_fall_back_does_not_allow_hidden_out_of_hours_middle():
    """End points inside 02:00-02:15 are insufficient across the autumn fold."""
    from psycopg.types.json import Jsonb
    autumn_start=datetime(2030,10,27,2,10,tzinfo=ZONE,fold=0)
    autumn_end=datetime(2030,10,27,2,5,tzinfo=ZONE,fold=1)
    assert autumn_end.astimezone(timezone.utc)>autumn_start.astimezone(timezone.utc)
    spring_start=datetime(2030,3,31,1,30,tzinfo=ZONE)
    spring_end=datetime(2030,3,31,3,30,tzinfo=ZONE)
    with psycopg.connect(DSN) as db:
        def allows(open_min,close_min,begin,end):
            return db.execute(
                """SELECT auto_location_hours_allow(
                    'Europe/Berlin',%s::jsonb,%s::date[],
                    %s::timestamptz,%s::timestamptz)""",
                (Jsonb([{"weekday":7,"opensMinute":open_min,
                         "closesMinute":close_min}]),
                 [],begin,end),
            ).fetchone()[0]
        assert allows(120,135,autumn_start,autumn_end) is False
        assert allows(60,240,autumn_start,autumn_end) is True
        assert allows(60,240,spring_start,spring_end) is True
