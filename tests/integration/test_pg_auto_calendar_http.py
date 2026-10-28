"""PG17 calendar reads current scoped booking only."""
from datetime import datetime,timedelta,timezone
from uuid import uuid4
import psycopg
from fastapi.testclient import TestClient
from roboport_api.main import app
from roboport_api.auto_appointment_service import create_appointment,reschedule_appointment
from test_pg_auto_vehicle_claim_http import _scenario
from test_pg_auto_bay_booking import _org_and_bay
from test_pg_baseline import DSN

def test_calendar_tenant_active_booking_and_limited_window(monkeypatch):
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTO_API","1")
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTH_API","1")
    monkeypatch.setenv("ROBOport_DATABASE_URL",DSN)
    d=_scenario()
    with psycopg.connect(DSN) as db:
        org,loc,bay=_org_and_bay(db)
        other,other_loc,_=_org_and_bay(db)
        db.execute("UPDATE auto_vehicle_customer_link SET verified_at=now(),verified_by=%s WHERE id=%s",
                   (d["manager"],d["link"]))
        db.execute("INSERT INTO auto_vehicle_location_binding(vehicle_id,location_id) VALUES(%s,%s)",(d["vehicle"],loc))
        db.execute("""INSERT INTO auto_organization_role_grant
           (id,organization_id,location_id,user_id,role_code)
           VALUES(%s,%s,%s,%s,'INTAKE')""",(uuid4(),org,loc,d["intake"]))
    start=datetime.now(timezone.utc)+timedelta(days=2)
    with psycopg.connect(DSN) as db:
        a,old,w=create_appointment(db,d["tokens"][d["intake"]],org,loc,bay,d["link"],start,start+timedelta(hours=1))
        fresh=reschedule_appointment(db,d["tokens"][d["intake"]],org,a,bay,
                                      start+timedelta(hours=2),start+timedelta(hours=3))
    client=TestClient(app,base_url="https://127.0.0.1")
    url=f"/api/v1/dev/auto/organizations/{org}/calendar"
    args={"locationId":str(loc),"startsAt":start.isoformat(),
          "endsAt":(start+timedelta(days=1)).isoformat()}
    assert client.get(url,params=args).status_code==403
    client.cookies.set("rp_session",d["tokens"][d["intake"]])
    response=client.get(url,params=args)
    assert response.status_code==200,response.text
    assert response.json()["items"]==[{"appointmentId":str(a),"bookingId":str(fresh),
         "bayId":str(bay),"startsAt":(start+timedelta(hours=2)).isoformat(),
         "endsAt":(start+timedelta(hours=3)).isoformat(),"workOrderId":str(w),
         "employeeId":None}]
    assert response.json()["hasMore"] is False
    assert "customerId" not in response.text and "vehicleId" not in response.text
    wrong=f"/api/v1/dev/auto/organizations/{other}/calendar"
    assert client.get(wrong,params=args).status_code==403
    assert client.get(url,params={**args,"locationId":str(other_loc)}).status_code==403
    assert client.get(url,params={**args,"endsAt":(start+timedelta(days=10)).isoformat()}).status_code==403


def test_calendar_101_rows_signals_truncation_without_cross_tenant_data(monkeypatch):
    """PG17 boundary: 100 items returned, 101st detected, no customer metadata."""
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTO_API","1")
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTH_API","1")
    monkeypatch.setenv("ROBOport_DATABASE_URL",DSN)
    d=_scenario()
    with psycopg.connect(DSN) as db:
        org,loc,bay=_org_and_bay(db)
        db.execute("UPDATE auto_vehicle_customer_link SET verified_at=now(),verified_by=%s WHERE id=%s",
                   (d["manager"],d["link"]))
        db.execute("INSERT INTO auto_vehicle_location_binding(vehicle_id,location_id) VALUES(%s,%s)",
                   (d["vehicle"],loc))
        db.execute("""INSERT INTO auto_organization_role_grant
           (id,organization_id,location_id,user_id,role_code)
           VALUES(%s,%s,%s,%s,'INTAKE')""",(uuid4(),org,loc,d["intake"]))
        vehicle=db.execute("SELECT vehicle_id FROM auto_vehicle_customer_link WHERE id=%s",
                           (d["link"],)).fetchone()[0]
        start=datetime.now(timezone.utc)+timedelta(days=3)
        for index in range(101):
            booking,appointment,order=uuid4(),uuid4(),uuid4()
            begins=start+timedelta(minutes=10*index)
            db.execute("""INSERT INTO auto_bay_booking
                (id,organization_id,location_id,bay_id,starts_at,ends_at)
                VALUES(%s,%s,%s,%s,%s,%s)""",
                (booking,org,loc,bay,begins,begins+timedelta(minutes=5)))
            db.execute("""INSERT INTO auto_appointment
                (id,booking_id,organization_id,location_id,vehicle_id,customer_link_id)
                VALUES(%s,%s,%s,%s,%s,%s)""",
                (appointment,booking,org,loc,vehicle,d["link"]))
            db.execute("""INSERT INTO auto_work_order
                (id,appointment_id,organization_id,location_id)
                VALUES(%s,%s,%s,%s)""",(order,appointment,org,loc))
    client=TestClient(app,base_url="https://127.0.0.1")
    client.cookies.set("rp_session",d["tokens"][d["intake"]])
    url=f"/api/v1/dev/auto/organizations/{org}/calendar"
    args={"locationId":str(loc),"startsAt":start.isoformat(),
          "endsAt":(start+timedelta(days=1)).isoformat()}
    response=client.get(url,params=args)
    assert response.status_code==200,response.text
    payload=response.json()
    assert len(payload["items"])==100
    assert all(x["employeeId"] is None for x in payload["items"])
    assert payload["hasMore"] is True
    assert payload["nextStartsAt"]==payload["items"][-1]["startsAt"]
    assert payload["nextAppointmentId"]==payload["items"][-1]["appointmentId"]
    second=client.get(url,params={**args,"afterStartsAt":payload["nextStartsAt"],
                                  "afterAppointmentId":payload["nextAppointmentId"]})
    assert second.status_code==200,second.text
    assert len(second.json()["items"])==1
    assert second.json()["hasMore"] is False
    assert "nextStartsAt" not in second.json()
    assert second.json()["items"][0]["appointmentId"] not in {
        item["appointmentId"] for item in payload["items"]}
    assert client.get(url,params={**args,"afterStartsAt":payload["nextStartsAt"]}).status_code==403
    assert client.get(url,params={**args,"afterStartsAt":"bad",
        "afterAppointmentId":payload["nextAppointmentId"]}).status_code==422
    assert len({item["appointmentId"] for item in payload["items"]})==100
    assert [item["startsAt"] for item in payload["items"]]==sorted(
        item["startsAt"] for item in payload["items"])
    assert "customerId" not in response.text and "vehicleId" not in response.text
    # Exactly 100 overlapping bookings must not report a phantom next page.
    boundary=client.get(url,params={**args,
        "endsAt":(start+timedelta(minutes=1000)).isoformat()})
    assert boundary.status_code==200,boundary.text
    assert len(boundary.json()["items"])==100
    assert boundary.json()["hasMore"] is False
    assert client.get(url,params={**args,"locationId":str(uuid4())}).status_code==403


def test_calendar_reports_only_current_employee_assignment_after_reschedule(monkeypatch):
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTO_API","1")
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTH_API","1")
    monkeypatch.setenv("ROBOport_DATABASE_URL",DSN)
    d=_scenario()
    start=datetime.now(timezone.utc)+timedelta(days=4)
    with psycopg.connect(DSN) as db:
        org,loc,bay=_org_and_bay(db)
        db.execute("UPDATE auto_vehicle_customer_link SET verified_at=now(),verified_by=%s WHERE id=%s",
                   (d["manager"],d["link"]))
        db.execute("INSERT INTO auto_vehicle_location_binding(vehicle_id,location_id) VALUES(%s,%s)",
                   (d["vehicle"],loc))
        db.execute("""INSERT INTO auto_organization_role_grant
          (id,organization_id,location_id,user_id,role_code)
          VALUES(%s,%s,%s,%s,'INTAKE')""",(uuid4(),org,loc,d["intake"]))
        db.execute("""INSERT INTO auto_staff_shift
          (id,organization_id,location_id,user_id,starts_at,ends_at)
          VALUES(%s,%s,%s,%s,%s,%s)""",
          (uuid4(),org,loc,d["intake"],start,start+timedelta(hours=8)))
    token=d["tokens"][d["intake"]]
    with psycopg.connect(DSN) as db:
        appointment,old,_=create_appointment(db,token,org,loc,bay,d["link"],start,
                      start+timedelta(hours=1),d["intake"])
    client=TestClient(app,base_url="https://127.0.0.1")
    client.cookies.set("rp_session",token)
    url=f"/api/v1/dev/auto/organizations/{org}/calendar"
    args={"locationId":str(loc),"startsAt":start.isoformat(),
          "endsAt":(start+timedelta(days=1)).isoformat()}
    first=client.get(url,params=args)
    assert first.status_code==200,first.text
    assert first.json()["items"][0]["employeeId"]==str(d["intake"])
    with psycopg.connect(DSN) as db:
        new=reschedule_appointment(db,token,org,appointment,bay,
            start+timedelta(hours=2),start+timedelta(hours=3),clear_employee=True)
    changed=client.get(url,params=args)
    assert changed.status_code==200,changed.text
    assert len(changed.json()["items"])==1
    assert changed.json()["items"][0]["bookingId"]==str(new)
    assert changed.json()["items"][0]["employeeId"] is None
    with psycopg.connect(DSN) as db:
        assert db.execute("SELECT canceled_at IS NOT NULL FROM auto_employee_reservation WHERE booking_id=%s",
                          (old,)).fetchone()==(True,)
    assert "customerId" not in changed.text and "vehicleId" not in changed.text
