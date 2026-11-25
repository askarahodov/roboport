"""PG17 scoped slot candidate read is bounded, shift-aware and never a booking."""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import psycopg
from fastapi.testclient import TestClient

from roboport_api.main import app
from roboport_api.auto_slot_candidates import candidate_slots
from test_pg_auto_vehicle_claim_http import _scenario
from test_pg_auto_bay_booking import _org_and_bay
from test_pg_baseline import DSN


def test_slot_candidates_scope_bay_conflicts_shift_and_http(monkeypatch):
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTO_API", "1")
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTH_API", "1")
    monkeypatch.setenv("ROBOport_DATABASE_URL", DSN)
    d = _scenario()
    start = (datetime.now(timezone.utc) + timedelta(days=4)).replace(
        hour=0, minute=0, second=0, microsecond=0)
    with psycopg.connect(DSN) as db:
        org, loc, bay = _org_and_bay(db)
        other, other_loc, other_bay = _org_and_bay(db)
        db.execute("""INSERT INTO auto_organization_role_grant
          (id,organization_id,location_id,user_id,role_code)
          VALUES (%s,%s,%s,%s,'INTAKE')""",
          (uuid4(), org, loc, d["intake"]))
        db.execute("""INSERT INTO auto_staff_shift
          (id,organization_id,location_id,user_id,starts_at,ends_at)
          VALUES (%s,%s,%s,%s,%s,%s)""",
          (uuid4(), org, loc, d["intake"],
           start+timedelta(hours=9), start+timedelta(hours=13)))
        db.execute("""INSERT INTO auto_bay_booking
          (id,organization_id,location_id,bay_id,starts_at,ends_at)
          VALUES (%s,%s,%s,%s,%s,%s)""",
          (uuid4(), org, loc, bay,
           start+timedelta(hours=10), start+timedelta(hours=11)))
    token = d["tokens"][d["intake"]]
    with psycopg.connect(DSN) as db:
        available = candidate_slots(db,token,org,loc,bay,start,d["intake"])
        times = {x["startsAt"] for x in available}
        assert (start+timedelta(hours=9)).isoformat() in times
        assert (start+timedelta(hours=9,minutes=30)).isoformat() not in times
        assert (start+timedelta(hours=10)).isoformat() not in times
        assert (start+timedelta(hours=10,minutes=30)).isoformat() not in times
        assert (start+timedelta(hours=11)).isoformat() in times
        assert (start+timedelta(hours=12)).isoformat() in times
        assert (start+timedelta(hours=12,minutes=30)).isoformat() not in times
        assert len(candidate_slots(db,token,org,loc,bay,start)) <= 48
        assert not any("customer" in str(x).lower() for x in available)
    url=f"/api/v1/dev/auto/organizations/{org}/slot-candidates"
    params={"locationId":str(loc),"bayId":str(bay),
            "day":start.date().isoformat(),"employeeId":str(d["intake"])}
    client=TestClient(app,base_url="https://127.0.0.1")
    assert client.get(url,params=params).status_code==403
    client.cookies.set("rp_session",token)
    ok=client.get(url,params=params)
    assert ok.status_code==200,ok.text
    assert ok.json()["guaranteed"] is False
    assert ok.json()["items"]==available
    assert client.get(url,params={**params,"locationId":str(other_loc)}).status_code==403
    assert client.get(url,params={**params,"bayId":str(other_bay)}).status_code==403
    assert client.get(url.replace(str(org),str(other)),params=params).status_code==403
    assert client.get(url,params={**params,"day":"2030-02-30"}).status_code==422
    assert client.get(url,params={**params,"employeeId":"bad"}).status_code==422


def test_reschedule_slots_exclude_only_own_current_booking(monkeypatch):
    from roboport_api.auto_appointment_service import create_appointment,reschedule_appointment
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTO_API","1")
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTH_API","1")
    monkeypatch.setenv("ROBOport_DATABASE_URL",DSN)
    d=_scenario()
    start=(datetime.now(timezone.utc)+timedelta(days=5)).replace(
        hour=0,minute=0,second=0,microsecond=0)
    with psycopg.connect(DSN) as db:
        org,loc,bay=_org_and_bay(db)
        second_bay=uuid4()
        db.execute("""INSERT INTO auto_work_bay
          (id,organization_id,location_id,code)
          VALUES (%s,%s,%s,'SECOND')""",(second_bay,org,loc))
        db.execute("UPDATE auto_vehicle_customer_link SET verified_at=now(),verified_by=%s WHERE id=%s",
                   (d["manager"],d["link"]))
        db.execute("INSERT INTO auto_vehicle_location_binding(vehicle_id,location_id) VALUES (%s,%s)",
                   (d["vehicle"],loc))
        db.execute("""INSERT INTO auto_organization_role_grant
          (id,organization_id,location_id,user_id,role_code)
          VALUES (%s,%s,%s,%s,'INTAKE')""",(uuid4(),org,loc,d["intake"]))
        db.execute("""INSERT INTO auto_staff_shift
          (id,organization_id,location_id,user_id,starts_at,ends_at)
          VALUES (%s,%s,%s,%s,%s,%s)""",
          (uuid4(),org,loc,d["intake"],start+timedelta(hours=9),
           start+timedelta(hours=13)))
    token=d["tokens"][d["intake"]]
    at=start+timedelta(hours=10)
    with psycopg.connect(DSN) as db:
        appointment,booking,_=create_appointment(
          db,token,org,loc,bay,d["link"],at,at+timedelta(hours=1),d["intake"])
        no_context=candidate_slots(db,token,org,loc,second_bay,start,d["intake"])
        scoped=candidate_slots(db,token,org,loc,second_bay,start,d["intake"],appointment)
        same_bay=candidate_slots(db,token,org,loc,bay,start,d["intake"],appointment)
        assert at.isoformat() not in {x["startsAt"] for x in no_context}
        assert at.isoformat() in {x["startsAt"] for x in scoped}
        assert at.isoformat() not in {x["startsAt"] for x in same_bay}
        assert at.isoformat() not in {x["startsAt"] for x in candidate_slots(
            db,token,org,loc,bay,start,d["intake"])}
        moved=reschedule_appointment(db,token,org,appointment,second_bay,
                  at,at+timedelta(hours=1))
        assert db.execute("SELECT bay_id FROM auto_bay_booking WHERE id=%s",(moved,)).fetchone()==(second_bay,)
        other,other_loc,_=_org_and_bay(db)
        from roboport_api.auto_slot_candidates import SlotsDenied
        import pytest
        with pytest.raises(SlotsDenied):
            candidate_slots(db,token,org,loc,bay,start,d["intake"],uuid4())
        with pytest.raises(SlotsDenied):
            candidate_slots(db,token,other,other_loc,bay,start,d["intake"],appointment)
    client=TestClient(app,base_url="https://127.0.0.1")
    client.cookies.set("rp_session",token)
    url=f"/api/v1/dev/auto/organizations/{org}/slot-candidates"
    params={"locationId":str(loc),"bayId":str(second_bay),
            "day":start.date().isoformat(),"employeeId":str(d["intake"]),
            "appointmentId":str(appointment)}
    response=client.get(url,params=params)
    assert response.status_code==200,response.text
    # After the successful move the second bay is occupied by that appointment.
    assert at.isoformat() not in {x["startsAt"] for x in response.json()["items"]}
    assert client.get(url,params={**params,"appointmentId":str(uuid4())}).status_code==403


def test_disabled_employee_is_not_offered_or_reserved_and_history_survives(monkeypatch):
    from test_pg_auto_booking_materials_atomic import _ready
    from test_pg_auto_customer_booking_http import _auth
    from test_pg_auto_stock_service import grant
    from roboport_api.notification_outbox import run_once
    import pytest

    d,org,loc,bay,http,part,consumable,_,service,_=_ready(monkeypatch)
    employee=d["manager"]
    day=(datetime.now(timezone.utc)+timedelta(days=35)).replace(
        hour=0,minute=0,second=0,microsecond=0)
    at=day+timedelta(hours=9)
    later=day+timedelta(hours=12)
    with psycopg.connect(DSN) as db:
        grant(db,employee,org,loc,"MECHANIC")
        db.execute("""INSERT INTO auto_staff_shift
          (id,organization_id,location_id,user_id,starts_at,ends_at)
          VALUES(%s,%s,%s,%s,%s,%s)""",
          (uuid4(),org,loc,employee,at,day+timedelta(hours=16)))
    headers=_auth(http,d,d["intake"])
    root=f"/api/v1/dev/auto/organizations/{org}"
    slots=root+"/slot-candidates"
    query={"locationId":str(loc),"bayId":str(bay),"day":day.date().isoformat(),
           "employeeId":str(employee),"serviceId":str(service)}
    response=http.get(slots,params=query)
    assert response.status_code==200,response.text
    assert at.isoformat() in {x["startsAt"] for x in response.json()["items"]}
    assert response.json()["guaranteed"] is False
    create=root+"/appointments"
    payload={"locationId":str(loc),"bayId":str(bay),"customerLinkId":str(d["link"]),
             "employeeId":str(employee),"serviceIds":[str(service)],
             "startsAt":at.isoformat(),"endsAt":(at+timedelta(hours=1)).isoformat()}
    first=http.post(create,headers={**headers,"Idempotency-Key":str(uuid4())},json=payload)
    assert first.status_code==201,first.text
    appointment=first.json()["appointmentId"]
    booking=first.json()["bookingId"]
    order=first.json()["workOrderId"]
    history=root+f"/locations/{loc}/appointments/{appointment}/history"
    with psycopg.connect(DSN) as db:
        db.execute("UPDATE app_user SET status='DISABLED' WHERE id=%s",(employee,))
    directory=http.get(root+f"/locations/{loc}/booking-directory",params={"kind":"employees"})
    assert directory.status_code==200
    assert str(employee) not in {x["id"] for x in directory.json()["items"]}
    unavailable=http.get(slots,params=query)
    assert unavailable.status_code==200 and unavailable.json()["items"]==[]
    # Запрос по устаревшему окну не создаёт новый наряд или уведомление.
    body={**payload,"startsAt":later.isoformat(),
          "endsAt":(later+timedelta(hours=1)).isoformat()}
    refused=http.post(create,headers={**headers,"Idempotency-Key":str(uuid4())},json=body)
    assert refused.status_code==409,refused.text
    assert refused.headers["cache-control"]=="no-store"
    moving={**headers,"Idempotency-Key":str(uuid4())}
    move=create+"/"+appointment+"/reschedule"
    moved_body={"bayId":str(bay),"startsAt":body["startsAt"],"endsAt":body["endsAt"]}
    assert http.post(move,headers=moving,json=moved_body).status_code==409
    with psycopg.connect(DSN) as db:
        # Прямой INSERT подчиняется той же новой защите; история не меняется.
        other=uuid4()
        with pytest.raises(psycopg.errors.RaiseException,match="booking employee inactive"):
            with db.transaction():
                db.execute("""INSERT INTO auto_bay_booking
                  (id,organization_id,location_id,bay_id,starts_at,ends_at)
                  VALUES(%s,%s,%s,%s,%s,%s)""",(other,org,loc,bay,later,later+timedelta(hours=1)))
                db.execute("""INSERT INTO auto_employee_reservation
                  (id,booking_id,organization_id,location_id,employee_id,starts_at,ends_at)
                  VALUES(%s,%s,%s,%s,%s,%s,%s)""",
                  (uuid4(),other,org,loc,employee,later,later+timedelta(hours=1)))
        assert db.execute("SELECT count(*) FROM auto_appointment WHERE organization_id=%s",(org,)).fetchone()==(1,)
        assert db.execute("SELECT count(*) FROM auto_bay_booking WHERE organization_id=%s",(org,)).fetchone()==(1,)
        assert db.execute("SELECT canceled_at FROM auto_employee_reservation WHERE booking_id=%s",(booking,)).fetchone()==(None,)
        assert db.execute("SELECT count(*) FROM notification_outbox WHERE aggregate_id=%s",(appointment,)).fetchone()==(1,)
    original=http.get(history)
    assert original.status_code==200 and original.json()["workOrderId"]==order
    assert original.json()["items"][0]["state"]=="CURRENT"
    # Явно снять недоступного специалиста можно: запись и история сохраняются.
    cleared=http.post(move,headers=moving,json={**moved_body,"employeeMode":"clear"})
    assert cleared.status_code==200,cleared.text
    assert http.post(move,headers=moving,json={**moved_body,"employeeMode":"clear"}).json()==cleared.json()
    canceled=http.post(create+"/"+appointment+"/cancel",headers={**headers,"Idempotency-Key":str(uuid4())})
    assert canceled.status_code==200,canceled.text
    with psycopg.connect(DSN) as db:
        from uuid import UUID
        ident=UUID(appointment)
        outcome=run_once(db,"booking-acceptance-fail",simulate_failure=True,aggregate_id=ident)
        assert outcome["status"]=="RETRY"
        db.execute("""UPDATE notification_outbox SET available_at=now()-interval '1 minute'
          WHERE aggregate_id=%s AND status='PENDING'""",(ident,))
        assert run_once(db,"booking-acceptance-retry",aggregate_id=ident)["status"]=="DELIVERED"
        assert run_once(db,"booking-acceptance-late",aggregate_id=ident) is None
        assert db.execute("""SELECT event_type,status,attempts FROM notification_outbox
          WHERE aggregate_id=%s ORDER BY event_type""",(ident,)).fetchall()==[
              ("CANCELED","DELIVERED",2),("CREATED","SUPERSEDED",0),
              ("RESCHEDULED","SUPERSEDED",0)]
    final=http.get(history)
    assert final.status_code==200,final.text
    assert final.json()["cancellation"]["notificationStatus"]=="DELIVERED"
    assert final.json()["cancellation"]["attempts"]==2
    assert final.json()["realDelivery"] is False
    _auth(http,d,d["client_a"])
    from test_pg_auto_customer_booking_http import ROOT
    assert http.get(ROOT+"/appointments/"+appointment+"/history",params={
        "organizationId":str(org),"locationId":str(loc)}).json()==final.json()
