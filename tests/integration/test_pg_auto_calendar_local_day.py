"""Проверка IANA зон филиала, локального дня и DST без tenant leakage."""
import csv
from io import StringIO
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo

import psycopg
import pytest
from fastapi.testclient import TestClient

from roboport_api.auto_calendar import local_day_window, CalendarDenied, CalendarInvalid
from roboport_api.auto_slot_candidates import candidate_slots
from roboport_api.auto_appointment_service import create_appointment
from roboport_api.main import app
from test_pg_auto_vehicle_claim_http import _scenario
from test_pg_auto_bay_booking import _org_and_bay
from test_pg_baseline import DSN


def _setup_zone():
    d = _scenario()
    with psycopg.connect(DSN) as db:
        org, loc, bay = _org_and_bay(db)
        db.execute(
            "UPDATE auto_service_location SET timezone_name='Europe/Berlin' WHERE id=%s",
            (loc,),
        )
        db.execute(
            "UPDATE auto_vehicle_customer_link SET verified_at=now(),verified_by=%s WHERE id=%s",
            (d["manager"], d["link"]),
        )
        db.execute(
            "INSERT INTO auto_vehicle_location_binding(vehicle_id,location_id) VALUES(%s,%s)",
            (d["vehicle"], loc),
        )
        db.execute(
            """INSERT INTO auto_organization_role_grant
               (id,organization_id,location_id,user_id,role_code)
               VALUES(%s,%s,%s,%s,'INTAKE')""",
            (uuid4(), org, loc, d["intake"]),
        )
    return d, org, loc, bay


def test_local_calendar_dst_boundaries_and_api_scope(monkeypatch):
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTO_API", "1")
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTH_API", "1")
    monkeypatch.setenv("ROBOport_DATABASE_URL", DSN)
    d, org, loc, bay = _setup_zone()
    token = d["tokens"][d["intake"]]
    with psycopg.connect(DSN) as db:
        spring, spring_end, zone = local_day_window(db, token, org, loc, "2030-03-31")
        assert zone == "Europe/Berlin"
        assert spring.astimezone(timezone.utc).isoformat() == "2030-03-30T23:00:00+00:00"
        assert spring_end.astimezone(timezone.utc).isoformat() == "2030-03-31T22:00:00+00:00"
        assert spring_end.astimezone(timezone.utc) - spring.astimezone(timezone.utc) == timedelta(hours=23)
        autumn, autumn_end, _ = local_day_window(db, token, org, loc, "2030-10-27")
        assert autumn_end.astimezone(timezone.utc) - autumn.astimezone(timezone.utc) == timedelta(hours=25)
        assert len(candidate_slots(
            db, token, org, loc, bay, spring, day_end=spring_end
        )) == 45
        assert len(candidate_slots(
            db, token, org, loc, bay, autumn, day_end=autumn_end
        )) == 49
        with pytest.raises(CalendarInvalid):
            local_day_window(db, token, org, loc, "2030-02-30")
        with pytest.raises(CalendarDenied):
            local_day_window(db, d["tokens"][d["manager"]], org, loc, "2030-03-31")

        begins = spring.astimezone(timezone.utc) + timedelta(minutes=20)
        appointment, booking, work = create_appointment(
            db, token, org, loc, bay, d["link"], begins, begins+timedelta(minutes=30)
        )
        # После брони запрещается тихо менять IANA зону местной истории.
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("UPDATE auto_service_location SET timezone_name='UTC' WHERE id=%s", (loc,))
    client = TestClient(app, base_url="https://127.0.0.1")
    url = f"/api/v1/dev/auto/organizations/{org}/calendar"
    params = {"locationId": str(loc), "localDay": "2030-03-31"}
    assert client.get(url, params=params).status_code == 403
    client.cookies.set("rp_session", token)
    ok = client.get(url, params=params)
    assert ok.status_code == 200, ok.text
    assert ok.json()["timeZone"] == "Europe/Berlin"
    assert len(ok.json()["items"]) == 1
    assert ok.json()["items"][0]["appointmentId"] == str(appointment)
    assert ok.json()["items"][0]["workOrderId"] == str(work)
    slot_url = f"/api/v1/dev/auto/organizations/{org}/slot-candidates"
    slot_params = {"locationId":str(loc),"bayId":str(bay),"localDay":"2030-10-27"}
    slots = client.get(slot_url, params=slot_params)
    assert slots.status_code == 200, slots.text
    assert slots.json()["timeZone"] == "Europe/Berlin"
    assert slots.json()["guaranteed"] is False
    assert len(slots.json()["items"]) == 49
    assert client.get(slot_url, params={**slot_params,"day":"2030-10-27"}).status_code == 422
    assert client.get(slot_url, params={**slot_params,"localDay":"2030-02-30"}).status_code == 422
    assert client.get(url,params={**params,"localDay":"2030-02-30"}).status_code == 422
    assert client.get(url,params={**params,"startsAt":"2030-03-31T00:00:00Z"}).status_code == 422
    assert client.get(url,params={"locationId":str(loc)}).status_code == 422
    assert client.get(url,params={**params,"locationId":str(uuid4())}).status_code == 403
    assert "vehicleId" not in ok.text and "customerId" not in ok.text


def test_bad_timezone_rejected_without_workshop_records():
    with psycopg.connect(DSN) as db:
        org, loc, bay = _org_and_bay(db)
        for zone in ("UTC+1", "CET", "Fake/Invalid", "Europe/Berlin;DROP", ""):
            with pytest.raises((psycopg.errors.RaiseException, psycopg.errors.CheckViolation)):
                with db.transaction():
                    db.execute("UPDATE auto_service_location SET timezone_name=%s WHERE id=%s", (zone,loc))
        assert db.execute(
            "SELECT timezone_name FROM auto_service_location WHERE id=%s",(loc,)
        ).fetchone()[0] == "UTC"



def test_week_calendar_complete_dst_current_booking_and_live_revocation(monkeypatch):
    """One week overview counts only current active bookings of an INTAKE branch."""
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTO_API","1")
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTH_API","1")
    monkeypatch.setenv("ROBOport_DATABASE_URL",DSN)
    d,org,loc,bay=_setup_zone()
    token=d["tokens"][d["intake"]]
    start_day="2030-03-31"  # spring DST: 23h, not a fixed UTC day
    with psycopg.connect(DSN) as db:
        start,end,zone=local_day_window(db,token,org,loc,start_day)
        assert (end.astimezone(timezone.utc)-start.astimezone(timezone.utc))==timedelta(hours=23)
        a1,_,_=create_appointment(db,token,org,loc,bay,d["link"],
                                  start+timedelta(minutes=20),
                                  start+timedelta(minutes=50))
        # Crosses local midnight and therefore appears on both days.
        a2,_,_=create_appointment(db,token,org,loc,bay,d["link"],
                                  end-timedelta(minutes=20),
                                  end+timedelta(minutes=20))
    client=TestClient(app,base_url="https://127.0.0.1")
    path=f"/api/v1/dev/auto/organizations/{org}/calendar/week"
    args={"locationId":str(loc),"localDay":start_day}
    assert client.get(path,params=args).status_code==403
    client.cookies.set("rp_session",d["tokens"][d["manager"]])
    assert client.get(path,params=args).status_code==403
    client.cookies.set("rp_session",token)
    response=client.get(path,params=args)
    assert response.status_code==200,response.text
    assert response.headers["cache-control"]=="no-store"
    data=response.json()
    assert data["syntheticOnly"] is True
    assert data["timeZone"]=="Europe/Berlin"
    assert data["fromDay"]==start_day
    assert len(data["items"])==7
    assert data["items"][0]=={"localDay":"2030-03-31",
                             "appointments":2,"occupiedBays":1}
    assert data["items"][1]=={"localDay":"2030-04-01",
                             "appointments":1,"occupiedBays":1}
    assert all(item["appointments"]==0 and item["occupiedBays"]==0
               for item in data["items"][2:])
    for secret in (str(a1),str(a2),str(d["vehicle"]),str(d["ca"]),str(d["intake"])):
        assert secret not in response.text
    assert client.get(path,params={**args,"localDay":"2030-02-30"}).status_code==422
    assert client.get(path,params={**args,"localDay":"9999-12-31"}).status_code==422
    assert client.get(path,params={**args,"locationId":str(uuid4())}).status_code==403
    with psycopg.connect(DSN) as db:
        db.execute(
            """UPDATE auto_organization_role_grant SET revoked_at=now()
               WHERE organization_id=%s AND location_id=%s AND user_id=%s
                 AND role_code='INTAKE'""",(org,loc,d["intake"]))
    assert client.get(path,params=args).status_code==403


def test_week_csv_export_all_seven_days_and_live_authorization(monkeypatch):
    """CSV must be recalculated with current grants and no appointment identifiers."""
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTO_API", "1")
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTH_API", "1")
    monkeypatch.setenv("ROBOport_DATABASE_URL", DSN)
    d, org, loc, bay = _setup_zone()
    token = d["tokens"][d["intake"]]
    with psycopg.connect(DSN) as db:
        start, end, _ = local_day_window(db, token, org, loc, "2030-10-27")
        assert end.astimezone(timezone.utc) - start.astimezone(timezone.utc) == timedelta(hours=25)
        appointment, _, work = create_appointment(
            db, token, org, loc, bay, d["link"],
            start + timedelta(minutes=20), start + timedelta(minutes=50),
        )
    client = TestClient(app, base_url="https://127.0.0.1")
    url = f"/api/v1/dev/auto/organizations/{org}/calendar/week/export"
    params = {"locationId": str(loc), "localDay": "2030-10-27"}
    assert client.get(url, params=params).status_code == 403
    client.cookies.set("rp_session", d["tokens"][d["manager"]])
    assert client.get(url, params=params).status_code == 403
    client.cookies.set("rp_session", token)
    response = client.get(url, params=params)
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["content-disposition"] == (
        'attachment; filename="auto-calendar-week.csv"'
    )
    assert response.text.startswith("\ufeffДата филиала;")
    rows = list(csv.reader(StringIO(response.text.removeprefix("\ufeff")), delimiter=";"))
    assert len(rows) == 8 and all(len(row) == 3 for row in rows)
    assert rows[0] == ["Дата филиала", "Действующие записи", "Занятые посты"]
    assert rows[1] == ["2030-10-27", "1", "1"]
    assert rows[2] == ["2030-10-28", "0", "0"]
    assert rows[-1] == ["2030-11-02", "0", "0"]
    for secret in (str(appointment), str(work), str(d["vehicle"]),
                   str(d["ca"]), str(d["intake"]), str(bay)):
        assert secret not in response.text
    assert client.get(url, params={**params, "localDay": "2030-02-30"}).status_code == 422
    assert client.get(url, params={**params, "locationId": str(uuid4())}).status_code == 403
    with psycopg.connect(DSN) as db:
        db.execute(
            """UPDATE auto_organization_role_grant SET revoked_at=now()
               WHERE organization_id=%s AND location_id=%s AND user_id=%s
                 AND role_code='INTAKE'""", (org, loc, d["intake"]),
        )
    denied = client.get(url, params=params)
    assert denied.status_code == 403
    assert "Дата филиала" not in denied.text
