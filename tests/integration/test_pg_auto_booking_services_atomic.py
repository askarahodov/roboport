"""PG17 + HTTPS: выбор услуг связан с бронью и WorkOrder атомарно (#113)."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import hashlib
import json
import pytest
from uuid import uuid4

import psycopg
from fastapi.testclient import TestClient

from roboport_api.auto_appointment_service import (
    create_appointment, AppointmentDenied, AppointmentDurationInvalid,
)
from roboport_api.main import app
from test_pg_auto_customer_booking_http import _setup, _auth, ROOT
from test_pg_baseline import DSN


def _catalog(org, loc, other):
    first, second, long, foreign = [uuid4() for _ in range(4)]
    with psycopg.connect(DSN) as db:
        for ident, o, l, code, minutes in (
            (first, org, loc, "BOOK-001", 30),
            (second, org, loc, "BOOK-002", 30),
            (long, org, loc, "BOOK-003", 45),
            (foreign, other[0], other[1], "BOOK-FOREIGN", 30),
        ):
            db.execute(
                """INSERT INTO auto_service_catalog
                   (id,organization_id,location_id,code,title,duration_minutes)
                   VALUES(%s,%s,%s,%s,'SYNTHETIC SERVICE',%s)""",
                (ident, o, l, code, minutes),
            )
    return first, second, long, foreign


def test_customer_catalog_and_atomic_services_booking_http(monkeypatch):
    d, org, loc, bay, other, _, http = _setup(monkeypatch)
    with psycopg.connect(DSN) as db:
        other_loc = db.execute(
            "SELECT id FROM auto_service_location WHERE organization_id=%s LIMIT 1",
            (other,),
        ).fetchone()[0]
    first, second, long, foreign = _catalog(org, loc, (other, other_loc))
    catalog = ROOT + "/services"
    params = {"organizationId":str(org), "locationId":str(loc),
              "customerLinkId":str(d["link"])}
    assert http.get(catalog, params=params).status_code == 403
    _auth(http, d, d["client_b"])
    assert http.get(catalog, params=params).status_code == 403
    headers = _auth(http, d, d["client_a"])
    read = http.get(catalog, params=params)
    assert read.status_code == 200, read.text
    assert read.headers["cache-control"] == "no-store"
    assert {x["id"] for x in read.json()["items"]} == {str(first),str(second),str(long)}
    assert str(foreign) not in read.text
    assert "vin" not in read.text.lower()
    now = datetime.now(timezone.utc) + timedelta(days=26)
    payload = {"organizationId":str(org), "locationId":str(loc),
               "customerLinkId":str(d["link"]), "bayId":str(bay),
               "startsAt":now.isoformat(),
               "endsAt":(now+timedelta(hours=1)).isoformat()}
    url = ROOT + "/appointments"
    key = {**headers,"Idempotency-Key":str(uuid4())}
    # Запрет чужих услуг, превышения времени и повторных UUID: no phantom rows.
    for selected in ([str(first),str(foreign)], [str(first),str(first)]):
        forbidden = http.post(url,json={**payload,"serviceIds":selected},
                              headers={**headers,"Idempotency-Key":str(uuid4())})
        assert forbidden.status_code == 403, forbidden.text
    too_long = http.post(url,json={**payload,"serviceIds":[str(first),str(long)]},
                         headers={**headers,"Idempotency-Key":str(uuid4())})
    assert too_long.status_code == 422, too_long.text
    assert too_long.json()["code"] == "SERVICES_EXCEED_SLOT"
    malformed = http.post(url,json={**payload,"serviceIds":[str(first)]*9},
                          headers={**headers,"Idempotency-Key":str(uuid4())})
    assert malformed.status_code in (403, 422), malformed.text
    with psycopg.connect(DSN) as db:
        assert db.execute(
            "SELECT count(*) FROM auto_work_order WHERE organization_id=%s",
            (org,),
        ).fetchone()[0] == 0
        assert db.execute(
            """SELECT count(*) FROM notification_outbox
               WHERE aggregate_type='auto_appointment'
                 AND aggregate_id IN (
                   SELECT id FROM auto_appointment WHERE organization_id=%s
                 )""",
            (org,),
        ).fetchone()[0] == 0
    selected = {**payload,"serviceIds":[str(first),str(second)]}
    created = http.post(url,json=selected,headers=key)
    assert created.status_code == 201, created.text
    replay = http.post(url,json={**selected,"serviceIds":[str(second),str(first)]},
                       headers=key)
    assert replay.status_code == 201 and replay.json() == created.json()
    assert http.post(url,json={**payload,"serviceIds":[str(first)]},
                     headers=key).status_code == 409
    order = created.json()["workOrderId"]
    appointment = created.json()["appointmentId"]
    with psycopg.connect(DSN) as db:
        ids = db.execute(
            "SELECT service_id FROM auto_work_order_service WHERE work_order_id=%s",
            (order,),
        ).fetchall()
        assert {row[0] for row in ids} == {first,second}
        assert db.execute(
            "SELECT count(*) FROM auto_appointment WHERE organization_id=%s",
            (org,),
        ).fetchone()[0] == 1
        assert db.execute(
            """SELECT count(*) FROM notification_outbox
               WHERE aggregate_type='auto_appointment' AND aggregate_id=%s""",
            (appointment,),
        ).fetchone()[0] == 1
    # После включения работ клиент не может незаметно отменить оформленный наряд.
    assert http.post(url+"/"+appointment+"/cancel",
                     json={"organizationId":str(org)},headers=headers).status_code == 403


def test_staff_create_booking_and_selected_service_share_one_transaction(monkeypatch):
    d, org, loc, bay, other, _, http = _setup(monkeypatch)
    with psycopg.connect(DSN) as db:
        other_loc = db.execute(
            "SELECT id FROM auto_service_location WHERE organization_id=%s LIMIT 1",
            (other,),
        ).fetchone()[0]
    first, second, long, foreign = _catalog(org, loc, (other, other_loc))
    from test_pg_auto_stock_service import grant
    with psycopg.connect(DSN) as db:
        grant(db, d["intake"], org, loc, "INTAKE")
    staff_catalog = f"/api/v1/dev/auto/organizations/{org}/locations/{loc}/booking-services"
    assert http.get(staff_catalog).status_code == 403
    http.cookies.set("rp_session", d["tokens"][d["intake"]])
    own = http.get(staff_catalog)
    assert own.status_code == 200, own.text
    assert {item["id"] for item in own.json()["items"]} == {
        str(first),str(second),str(long),
    }
    assert str(foreign) not in own.text
    assert http.get(staff_catalog.replace(str(loc),str(other_loc))).status_code == 403
    token = d["tokens"][d["intake"]]
    starts = datetime.now(timezone.utc)+timedelta(days=27)
    with psycopg.connect(DSN) as db:
        # Ошибочный филиал не должен оставлять booking, appointment или outbox.
        try:
            create_appointment(db,token,org,loc,bay,d["link"],
                               starts,starts+timedelta(hours=1),
                               service_ids=(first,foreign))
            assert False, "cross-tenant service accepted"
        except AppointmentDenied:
            pass
        assert db.execute(
            "SELECT count(*) FROM auto_bay_booking WHERE organization_id=%s",
            (org,),
        ).fetchone()[0] == 0
        appointment,booking,order = create_appointment(
            db,token,org,loc,bay,d["link"],starts,starts+timedelta(hours=1),
            request_key=uuid4(),service_ids=(first,second))
        assert db.execute(
            "SELECT count(*) FROM auto_work_order_service WHERE work_order_id=%s",
            (order,),
        ).fetchone()[0] == 2
        assert db.execute(
            """SELECT count(*) FROM notification_outbox
               WHERE aggregate_type='auto_appointment' AND aggregate_id=%s""",
            (appointment,),
        ).fetchone()[0] == 1



def test_iana_dst_elapsed_time_not_wall_clock_and_legacy_fingerprint(monkeypatch):
    d, org, loc, bay, other, _, http = _setup(monkeypatch)
    with psycopg.connect(DSN) as db:
        other_loc = db.execute(
            "SELECT id FROM auto_service_location WHERE organization_id=%s LIMIT 1",
            (other,),
        ).fetchone()[0]
    first, second, long, foreign = _catalog(org,loc,(other,other_loc))
    from test_pg_auto_stock_service import grant
    with psycopg.connect(DSN) as db:
        grant(db, d["intake"], org, loc, "INTAKE")
    token = d["tokens"][d["intake"]]
    berlin = ZoneInfo("Europe/Berlin")
    # Весной по часам 2 часа, но фактически 1 час: 75 минут не помещаются.
    spring_start = datetime(2030,3,31,1,30,tzinfo=berlin)
    spring_end = datetime(2030,3,31,3,30,tzinfo=berlin)
    with psycopg.connect(DSN) as db:
        with pytest.raises(AppointmentDurationInvalid):
            create_appointment(
                db, token, org, loc, bay, d["link"],
                spring_start, spring_end, service_ids=(first,long),
            )
        assert db.execute(
            "SELECT count(*) FROM auto_bay_booking WHERE organization_id=%s", (org,)
        ).fetchone()[0] == 0

        # Осенью 01:30 → повторные 02:30 фактически 2 часа, а не один.
        autumn_start = datetime(2030,10,27,1,30,tzinfo=berlin)
        autumn_end = datetime(2030,10,27,2,30,tzinfo=berlin,fold=1)
        key = uuid4()
        appt, booking, order = create_appointment(
            db, token, org, loc, bay, d["link"],
            autumn_start, autumn_end, request_key=key,
            service_ids=(first,long),
        )
        assert db.execute(
            "SELECT count(*) FROM auto_work_order_service WHERE work_order_id=%s",
            (order,),
        ).fetchone()[0] == 2

    with psycopg.connect(DSN) as db:
        # Пустой serviceIds совместим с fingerprint прежней версии сервера.
        start = datetime(2030,11,3,10,0,tzinfo=timezone.utc)
        ends = start+timedelta(hours=1)
        empty_key = uuid4()
        created = create_appointment(
            db, token, org, loc, bay, d["link"], start, ends,
            request_key=empty_key,
        )
        expected_body = {
            "location":str(loc),"bay":str(bay),"link":str(d["link"]),
            "employee":None,"starts":start.isoformat(),"ends":ends.isoformat(),
        }
        expected = hashlib.sha256(json.dumps(
            expected_body,sort_keys=True,separators=(",",":")
        ).encode()).hexdigest()
        stored = db.execute(
            """SELECT fingerprint FROM auto_appointment_idempotency
               WHERE organization_id=%s AND request_key=%s""",
            (org,empty_key),
        ).fetchone()[0]
        assert stored == expected
        assert create_appointment(
            db, token, org, loc, bay, d["link"], start, ends,
            request_key=empty_key,service_ids=(),
        ) == created
