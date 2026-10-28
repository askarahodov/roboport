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
        assert (start+timedelta(hours=9)).isoformat() not in times
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
