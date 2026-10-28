"""Real PG17 HTTP reschedule and cancellation after changing booking."""
from datetime import datetime,timedelta,timezone
from uuid import uuid4
import psycopg
from fastapi.testclient import TestClient
from roboport_api.main import app
from test_pg_auto_vehicle_claim_http import _scenario
from test_pg_auto_bay_booking import _org_and_bay
from test_pg_baseline import DSN

def test_reschedule_then_cancel_current_booking_and_audit(monkeypatch):
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTO_API","1")
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTH_API","1")
    monkeypatch.setenv("ROBOport_DATABASE_URL",DSN)
    d=_scenario()
    with psycopg.connect(DSN) as db:
        org,loc,bay=_org_and_bay(db)
        other,_,_=_org_and_bay(db)
        db.execute("UPDATE auto_vehicle_customer_link SET verified_at=now(),verified_by=%s WHERE id=%s",
                   (d["manager"],d["link"]))
        db.execute("INSERT INTO auto_vehicle_location_binding(vehicle_id,location_id) VALUES(%s,%s)",
                   (d["vehicle"],loc))
        db.execute("""INSERT INTO auto_organization_role_grant
          (id,organization_id,location_id,user_id,role_code)
          VALUES(%s,%s,%s,%s,'INTAKE')""",(uuid4(),org,loc,d["intake"]))
    client=TestClient(app,base_url="https://127.0.0.1")
    url=f"/api/v1/dev/auto/organizations/{org}/appointments"
    start=datetime.now(timezone.utc)+timedelta(days=5)
    client.cookies.set("rp_session",d["tokens"][d["intake"]])
    headers={"Origin":"https://127.0.0.1","X-CSRF-Token":d["csrf"][d["intake"]]}
    created=client.post(url,headers=headers,json={
      "locationId":str(loc),"bayId":str(bay),"customerLinkId":str(d["link"]),
      "startsAt":start.isoformat(),"endsAt":(start+timedelta(hours=1)).isoformat()})
    assert created.status_code==201,created.text
    appointment=created.json()["appointmentId"]
    old=created.json()["bookingId"]
    res=f"{url}/{appointment}/reschedule"
    payload={"bayId":str(bay),"startsAt":(start+timedelta(hours=3)).isoformat(),
             "endsAt":(start+timedelta(hours=4)).isoformat()}
    assert client.post(res,json=payload).status_code==403
    wrong=f"/api/v1/dev/auto/organizations/{other}/appointments/{appointment}/reschedule"
    assert client.post(wrong,json=payload,headers=headers).status_code==403
    moved=client.post(res,json=payload,headers=headers)
    assert moved.status_code==200,moved.text
    fresh=moved.json()["bookingId"]
    assert fresh!=old
    assert client.post(res,json=payload,headers=headers).status_code==409
    canceled=client.post(f"{url}/{appointment}/cancel",headers=headers)
    assert canceled.status_code==200,canceled.text
    assert canceled.json()["status"]=="CANCELED"
    assert client.post(f"{url}/{appointment}/cancel",headers=headers).json()["status"]=="ALREADY_CANCELED"
    with psycopg.connect(DSN) as db:
        assert db.execute("""SELECT canceled_at IS NOT NULL FROM auto_bay_booking WHERE id=%s""",
                          (fresh,)).fetchone()==(True,)
        assert db.execute("""SELECT actor_id FROM auto_appointment_reschedule
                             WHERE old_booking_id=%s""",(old,)).fetchone()==(d["intake"],)
        assert db.execute("""SELECT action FROM auto_appointment_event
                  WHERE appointment_id=%s ORDER BY id""",(appointment,)).fetchall()==[
                  ("CREATED",),("BOOKING_CANCELED",)]
        assert db.execute("""SELECT action FROM auto_work_order_event
                  WHERE work_order_id=%s ORDER BY id""",(created.json()["workOrderId"],)).fetchall()==[
                  ("CREATED",),("APPOINTMENT_CANCELED",)]
