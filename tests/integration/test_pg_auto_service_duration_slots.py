"""PG17: окна по реальной суммарной длительности услуг, изоляция и DST."""
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo

import psycopg

from roboport_api.auto_slot_candidates import candidate_slots
from test_pg_auto_customer_booking_http import _setup, _auth, ROOT
from test_pg_auto_booking_services_atomic import _catalog
from test_pg_auto_stock_service import grant
from test_pg_baseline import DSN


def _scenario(monkeypatch):
    d, org, loc, bay, other, _, http = _setup(monkeypatch)
    with psycopg.connect(DSN) as db:
        other_loc = db.execute(
            "SELECT id FROM auto_service_location WHERE organization_id=%s LIMIT 1",
            (other,),
        ).fetchone()[0]
    first, second, long, foreign = _catalog(org, loc, (other, other_loc))
    return d, org, loc, bay, other, other_loc, http, first, second, long, foreign


def test_customer_slots_follow_catalog_duration_and_avoid_occupied_bay(monkeypatch):
    d,org,loc,bay,other,other_loc,http,first,second,long,foreign = _scenario(monkeypatch)
    headers = _auth(http, d, d["client_a"])
    day = (datetime.now(timezone.utc)+timedelta(days=28)).date().isoformat()
    query = {
        "organizationId":str(org),"locationId":str(loc),
        "customerLinkId":str(d["link"]),"bayId":str(bay),"localDay":day,
    }
    endpoint = ROOT+"/slots"
    default = http.get(endpoint,params=query)
    assert default.status_code == 200, default.text
    assert default.json()["items"]
    assert all(
        datetime.fromisoformat(item["endsAt"])-datetime.fromisoformat(item["startsAt"])
        == timedelta(minutes=60) for item in default.json()["items"]
    )
    selected = {**query,"serviceId":[str(first),str(long)]}
    response = http.get(endpoint,params=selected)
    assert response.status_code == 200, response.text
    slots = response.json()["items"]
    assert slots and response.json()["guaranteed"] is False
    assert all(
        datetime.fromisoformat(item["endsAt"])-datetime.fromisoformat(item["startsAt"])
        == timedelta(minutes=75) for item in slots
    )
    # Кандидат 75 мин разрешает атомарную запись ровно на 75 мин.
    slot=slots[0]
    from uuid import uuid4
    created = http.post(ROOT+"/appointments",json={
        "organizationId":str(org),"locationId":str(loc),
        "customerLinkId":str(d["link"]),"bayId":str(bay),
        "startsAt":slot["startsAt"],"endsAt":slot["endsAt"],
        "serviceIds":[str(first),str(long)],
    },headers={**headers,"Idempotency-Key":str(uuid4())})
    assert created.status_code == 201, created.text
    after = http.get(endpoint,params=selected)
    assert after.status_code == 200
    assert not any(
        item["startsAt"]==slot["startsAt"] for item in after.json()["items"]
    )
    with psycopg.connect(DSN) as db:
        assert db.execute(
            "SELECT count(*) FROM auto_work_order_service WHERE work_order_id=%s",
            (created.json()["workOrderId"],),
        ).fetchone()[0] == 2
        assert db.execute(
            """SELECT count(*) FROM notification_outbox
               WHERE aggregate_type='auto_appointment' AND aggregate_id=%s""",
            (created.json()["appointmentId"],),
        ).fetchone()[0] == 1


def test_slot_selection_rejects_foreign_duplicate_and_unverified_identity(monkeypatch):
    d,org,loc,bay,other,other_loc,http,first,second,long,foreign = _scenario(monkeypatch)
    day = (datetime.now(timezone.utc)+timedelta(days=30)).date().isoformat()
    query = {
        "organizationId":str(org),"locationId":str(loc),
        "customerLinkId":str(d["link"]),"bayId":str(bay),"localDay":day,
    }
    path = ROOT+"/slots"
    assert http.get(path,params={**query,"serviceId":str(first)}).status_code == 403
    _auth(http,d,d["client_b"])
    assert http.get(path,params={**query,"serviceId":str(first)}).status_code == 403
    _auth(http,d,d["client_a"])
    assert http.get(path,params={**query,"serviceId":[str(first),str(foreign)]}).status_code == 403
    for ids in ([str(first),str(first)], ["bad"], [str(first)]*9):
        assert http.get(path,params={**query,"serviceId":ids}).status_code == 422
    assert http.get(path,params={**query,"serviceId":str(first)}).status_code == 200


def test_staff_shift_bounds_full_service_duration_and_foreign_scope(monkeypatch):
    d,org,loc,bay,other,other_loc,http,first,second,long,foreign = _scenario(monkeypatch)
    starts = (datetime.now(timezone.utc)+timedelta(days=32)).replace(
        hour=0,minute=0,second=0,microsecond=0,
    )
    with psycopg.connect(DSN) as db:
        grant(db,d["intake"],org,loc,"INTAKE")
        db.execute(
            """INSERT INTO auto_staff_shift
               (id,organization_id,location_id,user_id,starts_at,ends_at)
               VALUES(%s,%s,%s,%s,%s,%s)""",
            (uuid4(),org,loc,d["intake"],starts+timedelta(hours=9),
             starts+timedelta(hours=12)),
        )
    http.cookies.set("rp_session", d["tokens"][d["intake"]])
    url=f"/api/v1/dev/auto/organizations/{org}/slot-candidates"
    query={"locationId":str(loc),"bayId":str(bay),"day":starts.date().isoformat(),
           "employeeId":str(d["intake"]),"serviceId":[str(first),str(long)]}
    slots=http.get(url,params=query)
    assert slots.status_code==200,slots.text
    candidates={x["startsAt"] for x in slots.json()["items"]}
    assert (starts+timedelta(hours=9)).isoformat() in candidates
    assert (starts+timedelta(hours=10,minutes=30)).isoformat() in candidates
    assert (starts+timedelta(hours=11)).isoformat() not in candidates
    assert http.get(url,params={**query,"serviceId":str(foreign)}).status_code==403
    assert http.get(url,params={**query,"serviceId":["bad"]}).status_code==422


def test_local_dst_day_varied_service_duration_has_real_utc_bounds(monkeypatch):
    d,org,loc,bay,other,other_loc,http,first,second,long,foreign = _scenario(monkeypatch)
    _auth(http,d,d["client_a"])
    endpoint=ROOT+"/slots"
    for day,elapsed in (("2030-03-31",timedelta(hours=23)),
                        ("2030-10-27",timedelta(hours=25))):
        query={"organizationId":str(org),"locationId":str(loc),
               "customerLinkId":str(d["link"]),"bayId":str(bay),
               "localDay":day,"serviceId":[str(first),str(long)]}
        response=http.get(endpoint,params=query)
        assert response.status_code==200,response.text
        assert response.json()["timeZone"]=="Europe/Berlin"
        for item in response.json()["items"]:
            start=datetime.fromisoformat(item["startsAt"])
            end=datetime.fromisoformat(item["endsAt"])
            assert end-start==timedelta(minutes=75)
            local_start=start.astimezone(ZoneInfo("Europe/Berlin"))
            assert local_start.date().isoformat()==day
        assert response.json()["items"]
