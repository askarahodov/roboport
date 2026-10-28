"""PG17 HTTP contract: org intake, CSRF, conflict and actor-attributed revoke."""
from datetime import datetime,timedelta,timezone
from uuid import uuid4
import psycopg
from fastapi.testclient import TestClient
from roboport_api.main import app
from test_pg_auto_vehicle_claim_http import _scenario
from test_pg_auto_bay_booking import _org_and_bay
from test_pg_baseline import DSN

def test_closed_http_create_cancel_and_isolation(monkeypatch):
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTO_API","1")
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTH_API","1")
    monkeypatch.setenv("ROBOport_DATABASE_URL",DSN)
    d=_scenario()
    with psycopg.connect(DSN) as db:
        org,loc,bay=_org_and_bay(db)
        other,other_loc,other_bay=_org_and_bay(db)
        db.execute("UPDATE auto_vehicle_customer_link SET verified_at=now(),verified_by=%s WHERE id=%s",
                   (d["manager"],d["link"]))
        db.execute("INSERT INTO auto_vehicle_location_binding(vehicle_id,location_id) VALUES(%s,%s)",
                   (d["vehicle"],loc))
        db.execute("""INSERT INTO auto_organization_role_grant
          (id,organization_id,location_id,user_id,role_code)
          VALUES(%s,%s,%s,%s,'INTAKE')""",(uuid4(),org,loc,d["intake"]))
    client=TestClient(app,base_url="https://127.0.0.1")
    url=f"/api/v1/dev/auto/organizations/{org}/appointments"
    starts=datetime.now(timezone.utc)+timedelta(days=3)
    data={"locationId":str(loc),"bayId":str(bay),"customerLinkId":str(d["link"]),
          "startsAt":starts.isoformat(),"endsAt":(starts+timedelta(hours=1)).isoformat()}
    assert client.post(url,json=data).status_code==403
    client.cookies.set("rp_session",d["tokens"][d["intake"]])
    assert client.post(url,json=data).status_code==403
    headers={"Origin":"https://127.0.0.1","X-CSRF-Token":d["csrf"][d["intake"]]}
    wrong=f"/api/v1/dev/auto/organizations/{other}/appointments"
    assert client.post(wrong,json=data,headers=headers).status_code==403
    created=client.post(url,json=data,headers=headers)
    assert created.status_code==201,created.text
    ident=created.json()["appointmentId"]
    assert client.post(url,json=data,headers=headers).status_code==409
    cancel=f"{url}/{ident}/cancel"
    assert client.post(f"{wrong}/{ident}/cancel",headers=headers).status_code==403
    client.cookies.set("rp_session",d["tokens"][d["manager"]])
    manager_headers={"Origin":"https://127.0.0.1","X-CSRF-Token":d["csrf"][d["manager"]]}
    assert client.post(cancel,headers=manager_headers).status_code==403
    client.cookies.set("rp_session",d["tokens"][d["intake"]])
    result=client.post(cancel,headers=headers)
    assert result.status_code==200,result.text
    assert result.json()["status"]=="CANCELED"
    assert client.post(cancel,headers=headers).json()["status"]=="ALREADY_CANCELED"
    with psycopg.connect(DSN) as db:
        assert db.execute("""SELECT actor_id,action FROM auto_appointment_actor_action
             WHERE appointment_id=%s""",(ident,)).fetchall()==[(d["intake"],"CANCELED")]
        assert db.execute("""SELECT action FROM auto_appointment_event
             WHERE appointment_id=%s ORDER BY id""",(ident,)).fetchall()==[
             ("CREATED",),("BOOKING_CANCELED",)]
        assert db.execute("""SELECT e.action FROM auto_work_order_event e
             JOIN auto_work_order w ON w.id=e.work_order_id
             WHERE w.appointment_id=%s ORDER BY e.id""",(ident,)).fetchall()==[
             ("CREATED",),("APPOINTMENT_CANCELED",)]
