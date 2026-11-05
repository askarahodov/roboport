"""PG17/HTTPS: CRM source dedupe, audit, booking lineage and revocation."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from datetime import datetime,timedelta,timezone
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient

from roboport_api.main import app
from roboport_api.auto_crm_leads import create_lead,LeadConflict
from roboport_api.auto_appointment_service import create_appointment
from test_pg_auto_bay_occupancy_http import _fixture
from test_pg_baseline import DSN


def url(org,loc):
    return f"/api/v1/dev/auto/organizations/{org}/locations/{loc}/crm/leads"


def session(http,d,actor):
    http.cookies.set("rp_session",d["tokens"][actor])
    return {"Origin":"https://127.0.0.1","X-CSRF-Token":d["csrf"][actor]}


def test_full_lead_to_booking_and_immutable_history(monkeypatch):
    d,org,loc,bay,foreign,foreign_loc,http=_fixture(monkeypatch)
    base=url(org,loc)
    payload={"customerLinkId":str(d["link"]),"sourceCode":"WEB","requestKey":str(uuid4())}
    assert http.get(base).status_code==403
    assert http.post(base,json=payload).status_code==403
    headers=session(http,d,d["intake"])
    assert http.post(base,json=payload).status_code==403
    created=http.post(base,json=payload,headers=headers)
    assert created.status_code==201,created.text
    lead=created.json()
    assert lead["syntheticOnly"] is True and lead["status"]=="NEW"
    assert lead["ownerId"]==str(d["intake"]) and lead["events"]==[]
    assert lead["sourceCode"]=="WEB"
    assert http.post(base,json=payload,headers=headers).status_code==200
    assert http.post(base,json={**payload,"sourceCode":"PHONE"},headers=headers).status_code==409
    assert http.post(base,json={**payload,"requestKey":str(uuid4())},headers=headers).status_code==409
    ident=lead["leadId"]
    detail=base+"/"+ident
    assert http.get(detail).status_code==200
    assert len(http.get(base).json()["items"])==1
    bad=http.post(detail+"/stages",headers=headers,json={
        "stageCode":"BOOKED","appointmentId":str(uuid4()),
        "requestKey":str(uuid4()),
    })
    assert bad.status_code==409
    qualify_key=str(uuid4())
    qualify=http.post(detail+"/stages",headers=headers,json={
        "stageCode":"QUALIFIED","requestKey":qualify_key,
    })
    assert qualify.status_code==201,qualify.text
    assert qualify.json()["status"]=="QUALIFIED"
    again=http.post(detail+"/stages",headers=headers,json={
        "stageCode":"QUALIFIED","requestKey":qualify_key,
    })
    assert again.status_code==200 and again.json()["replayed"] is True
    assert len(again.json()["events"])==1
    assert http.post(detail+"/stages",headers=headers,json={
        "stageCode":"LOST","reasonCode":"PRICE","requestKey":qualify_key,
    }).status_code==409
    assert http.post(detail+"/stages",headers=headers,json={
        "stageCode":"BOOKED","appointmentId":str(uuid4()),
        "requestKey":str(uuid4()),
    }).status_code==409
    start=datetime.now(timezone.utc)+timedelta(days=11)
    with psycopg.connect(DSN) as db:
        appointment,booking,order=create_appointment(
            db,d["tokens"][d["intake"]],org,loc,bay,d["link"],
            start,start+timedelta(hours=1),
        )
    booked_key=str(uuid4())
    booked=http.post(detail+"/stages",headers=headers,json={
        "stageCode":"BOOKED","appointmentId":str(appointment),
        "requestKey":booked_key,
    })
    assert booked.status_code==201,booked.text
    assert booked.json()["status"]=="BOOKED"
    assert booked.json()["events"][-1]["appointmentId"]==str(appointment)
    assert http.post(detail+"/stages",headers=headers,json={
        "stageCode":"BOOKED","appointmentId":str(appointment),
        "requestKey":booked_key,
    }).status_code==200
    assert http.post(detail+"/stages",headers=headers,json={
        "stageCode":"LOST","reasonCode":"TIMING","requestKey":str(uuid4()),
    }).status_code==409
    with psycopg.connect(DSN) as db:
        assert db.execute("SELECT count(*) FROM auto_crm_lead WHERE id=%s",(ident,)).fetchone()[0]==1
        assert db.execute("SELECT count(*) FROM auto_crm_lead_stage WHERE lead_id=%s",(ident,)).fetchone()[0]==2
        for sql,parameters in (
            ("DELETE FROM auto_crm_lead_stage WHERE lead_id=%s",(ident,)),
            ("UPDATE auto_crm_lead SET source_code='PHONE' WHERE id=%s",(ident,)),
        ):
            with pytest.raises(psycopg.errors.RaiseException):
                with db.transaction():
                    db.execute(sql,parameters)
    assert http.get(url(foreign,foreign_loc)).status_code==403
    assert http.get(detail.replace(str(loc),str(foreign_loc))).status_code==403
    assert str(d["vehicle"]) not in http.get(detail).text
    with psycopg.connect(DSN) as db:
        db.execute("UPDATE auto_vehicle_customer_link SET revoked_at=now() WHERE id=%s",(d["link"],))
    assert http.get(detail).status_code==403
    assert http.get(base).json()["items"]==[]
    assert http.post(detail+"/stages",headers=headers,json={
        "stageCode":"LOST","reasonCode":"OTHER","requestKey":str(uuid4()),
    }).status_code==403


def test_loss_and_manager_access_revoke(monkeypatch):
    d,org,loc,bay,foreign,foreign_loc,http=_fixture(monkeypatch)
    base=url(org,loc)
    headers=session(http,d,d["intake"])
    lead=http.post(base,headers=headers,json={
        "customerLinkId":str(d["link"]),"sourceCode":"WALK_IN",
        "requestKey":str(uuid4()),
    }).json()["leadId"]
    detail=base+"/"+lead
    lost=http.post(detail+"/stages",headers=headers,json={
        "stageCode":"LOST","reasonCode":"NO_RESPONSE","requestKey":str(uuid4()),
    })
    assert lost.status_code==201,lost.text
    assert lost.json()["status"]=="LOST"
    assert lost.json()["events"][0]["reasonCode"]=="NO_RESPONSE"
    assert http.post(detail+"/stages",headers=headers,json={
        "stageCode":"QUALIFIED","requestKey":str(uuid4()),
    }).status_code==409
    manager=session(http,d,d["manager"])
    assert http.get(base).status_code==200
    assert http.get(detail).status_code==200
    assert http.post(detail+"/stages",headers=manager,json={
        "stageCode":"BOOKED","appointmentId":str(uuid4()),
        "requestKey":str(uuid4()),
    }).status_code==409
    with psycopg.connect(DSN) as db:
        db.execute(
            """UPDATE auto_organization_role_grant SET revoked_at=now()
               WHERE organization_id=%s AND location_id=%s
                 AND user_id=%s AND role_code='MANAGER'""",
            (org,loc,d["manager"]),
        )
    assert http.get(base).status_code==403
    assert http.get(detail).status_code==403
    assert http.post(detail+"/stages",headers=manager,json={
        "stageCode":"QUALIFIED","requestKey":str(uuid4()),
    }).status_code==403


def test_concurrent_same_key_create_and_one_lead_per_source(monkeypatch):
    d,org,loc,bay,*_= _fixture(monkeypatch)
    key=uuid4()
    barrier=Barrier(2)
    def write(_):
        barrier.wait(timeout=10)
        with psycopg.connect(DSN) as db:
            return create_lead(db,d["tokens"][d["intake"]],
                               org,loc,d["link"],"PHONE",key)
    with ThreadPoolExecutor(max_workers=2) as pool:
        items=list(pool.map(write,range(2)))
    assert items[0]["leadId"]==items[1]["leadId"]
    assert sorted(x["replayed"] for x in items)==[False,True]
    with psycopg.connect(DSN) as db:
        assert db.execute(
            "SELECT count(*) FROM auto_crm_lead WHERE organization_id=%s AND location_id=%s",
            (org,loc),
        ).fetchone()[0]==1
        with pytest.raises(LeadConflict):
            create_lead(db,d["tokens"][d["intake"]],org,loc,d["link"],"PHONE",uuid4())


def test_complete_lead_journal_keyset_28_items_acl_revoke_and_bad_cursor(monkeypatch):
    """A real PG17 tenant has >25 leads; all must be reachable without a gap."""
    d,org,loc,bay,foreign,foreign_loc,http=_fixture(monkeypatch)
    base=url(org,loc)
    session(http,d,d["intake"])
    original=http.post(base,headers={
        "Origin":"https://127.0.0.1","X-CSRF-Token":d["csrf"][d["intake"]],
    },json={"customerLinkId":str(d["link"]),"sourceCode":"WEB",
            "requestKey":str(uuid4())})
    assert original.status_code==201,original.text
    with psycopg.connect(DSN) as db:
        # Each distinct verified synthetic vehicle+customer proof produces
        # a separate immutable lead; no real customer or production records.
        for _ in range(27):
            vehicle,link,lead=uuid4(),uuid4(),uuid4()
            db.execute("INSERT INTO auto_vehicle(id,make,model) VALUES(%s,'Synthetic','Test')",
                       (vehicle,))
            db.execute(
                """INSERT INTO auto_vehicle_customer_link
                   (id,vehicle_id,customer_id,verified_at,verified_by)
                   VALUES(%s,%s,%s,now(),%s)""",
                (link,vehicle,d["ca"],d["manager"]),
            )
            db.execute(
                "INSERT INTO auto_vehicle_location_binding(vehicle_id,location_id) "
                "VALUES(%s,%s)",(vehicle,loc),
            )
            db.execute(
                """INSERT INTO auto_crm_lead
                   (id,organization_id,location_id,customer_link_id,owner_id,
                    source_code,request_key)
                   VALUES(%s,%s,%s,%s,%s,'WALK_IN',%s)""",
                (lead,org,loc,link,d["intake"],uuid4()),
            )
        expected=[str(r[0]) for r in db.execute(
            """SELECT id FROM auto_crm_lead WHERE organization_id=%s
               AND location_id=%s ORDER BY created_at DESC,id DESC""",
            (org,loc),
        ).fetchall()]
    first=http.get(base)
    assert first.status_code==200,first.text
    assert first.headers["cache-control"]=="no-store"
    page1=first.json()
    assert len(page1["items"])==25 and page1["nextCursor"]
    second=http.get(base,params={"cursor":page1["nextCursor"]})
    assert second.status_code==200,second.text
    page2=second.json()
    assert len(page2["items"])==3 and page2["nextCursor"] is None
    ids=[x["leadId"] for x in page1["items"]+page2["items"]]
    assert ids==expected
    assert len(set(ids))==28

    # Cursor is a position, never an authorization or tenant grant.
    assert http.get(url(foreign,foreign_loc),params={
        "cursor":page1["nextCursor"],
    }).status_code==403
    for bad in ("!", "a"*181, "====", "invalid"):
        assert http.get(base,params={"cursor":bad}).status_code==422
    http.cookies.set("rp_session",d["tokens"][d["client_a"]])
    assert http.get(base,params={"cursor":"invalid"}).status_code==403
    session(http,d,d["intake"])
    with psycopg.connect(DSN) as db:
        # Revoke a proof from the second page; the next request must
        # filter it before pagination and never leak a stale row.
        victim=page2["items"][0]["customerLinkId"]
        db.execute("UPDATE auto_vehicle_customer_link SET revoked_at=now() "
                   "WHERE id=%s",(victim,))
    after=http.get(base,params={"cursor":page1["nextCursor"]})
    assert after.status_code==200,after.text
    assert len(after.json()["items"])==2
    assert victim not in after.text


def test_lead_cursor_roundtrip_order_and_noncanonical_rejected():
    from datetime import datetime,timezone
    from uuid import UUID
    from roboport_api.auto_crm_leads import (
        _decode_lead_cursor,_encode_lead_cursor,LeadDenied,
    )
    date=datetime(2030,10,27,2,30,tzinfo=timezone.utc)
    key=UUID("11111111-1111-4111-8111-111111111111")
    cursor=_encode_lead_cursor(date,key)
    assert _decode_lead_cursor(cursor)==(date,key)
    for changed in (cursor+"=",cursor.lower(),cursor+"_", "eA"):
        if changed!=cursor:
            with pytest.raises(LeadDenied):
                _decode_lead_cursor(changed)
