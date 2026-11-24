"""PG17 + HTTPS: booking, services, PART/CONSUMABLE RESERVE in one transaction.

Only scoped synthetic stock, never payment, public access or real goods.
"""
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import psycopg
import pytest

from roboport_api.auto_appointment_service import (
    create_appointment, AppointmentDenied, AppointmentKeyConflict,
    AppointmentStockUnavailable,
)
from roboport_api.auto_work_order_stock import mutate_work_order_stock
from test_pg_auto_customer_booking_http import _setup, _auth, ROOT
from test_pg_auto_stock_ledger import sku, record, balance
from test_pg_auto_stock_service import grant
from test_pg_baseline import DSN


def _ready(monkeypatch, warehouse=True):
    d,org,loc,bay,foreign,_,http=_setup(monkeypatch)
    with psycopg.connect(DSN) as db:
        foreign_loc=db.execute(
            "SELECT id FROM auto_service_location WHERE organization_id=%s LIMIT 1",
            (foreign,),
        ).fetchone()[0]
        grant(db,d["intake"],org,loc,"INTAKE")
        warehouse_grant=grant(db,d["intake"],org,loc,"WAREHOUSE") if warehouse else None
        part,consumable,foreign_sku=sku(db,org,loc),sku(db,org,loc),sku(db,foreign,foreign_loc)
        record(db,part,"RECEIPT",2)
        record(db,consumable,"RECEIPT",5)
        record(db,foreign_sku,"RECEIPT",5)
        service=uuid4()
        db.execute(
            """INSERT INTO auto_service_catalog
               (id,organization_id,location_id,code,title,duration_minutes)
               VALUES(%s,%s,%s,'SYN-MATERIAL-BOOKING','SYNTHETIC WORK',30)""",
            (service,org,loc),
        )
    return d,org,loc,bay,http,part,consumable,foreign_sku,service,warehouse_grant


def _payload(d,org,loc,bay,service,part,consumable,when=None):
    when=when or datetime.now(timezone.utc)+timedelta(days=29)
    return {
        "locationId":str(loc),"bayId":str(bay),
        "customerLinkId":str(d["link"]),
        "startsAt":when.isoformat(),"endsAt":(when+timedelta(hours=1)).isoformat(),
        "serviceIds":[str(service)],
        "materialItems":[
            {"skuId":str(part),"kind":"PART","quantity":2},
            {"skuId":str(consumable),"kind":"CONSUMABLE","quantity":3},
        ],
    }


def test_staff_selects_parts_and_consumables_atomically_and_replays(monkeypatch):
    d,org,loc,bay,http,part,consumable,foreign_sku,service,grant_id=_ready(monkeypatch)
    path=f"/api/v1/dev/auto/organizations/{org}/locations/{loc}/booking-stock"
    assert http.get(path).status_code==403
    headers=_auth(http,d,d["intake"])
    listing=http.get(path)
    assert listing.status_code==200,listing.text
    assert listing.headers["cache-control"]=="no-store"
    assert listing.json()["syntheticOnly"] is True
    assert {r["skuId"] for r in listing.json()["items"]}=={str(part),str(consumable)}
    assert {r["available"] for r in listing.json()["items"]}=={2,5}
    assert str(foreign_sku) not in listing.text
    payload=_payload(d,org,loc,bay,service,part,consumable)
    create=f"/api/v1/dev/auto/organizations/{org}/appointments"
    idem={**headers,"Idempotency-Key":str(uuid4())}
    response=http.post(create,json=payload,headers=idem)
    assert response.status_code==201,response.text
    # Порядок выбранных SKU не должен менять fingerprint.
    retry=http.post(create,json={**payload,"materialItems":list(reversed(payload["materialItems"]))},
                    headers=idem)
    assert retry.status_code==201,retry.text
    assert retry.json()==response.json()
    order=response.json()["workOrderId"]
    appointment=response.json()["appointmentId"]
    with psycopg.connect(DSN) as db:
        rows=db.execute(
            """SELECT m.sku_id,m.item_kind,m.quantity,e.action,e.quantity
               FROM auto_appointment_material_selection m
               JOIN auto_work_order_stock_event e ON e.stock_event_id=m.stock_event_id
               WHERE m.work_order_id=%s ORDER BY m.sku_id""",
            (order,),
        ).fetchall()
        assert len(rows)==2
        assert {(row[0],row[1],row[2]) for row in rows}=={
            (part,"PART",2),(consumable,"CONSUMABLE",3),
        }
        assert all(row[3]=="RESERVE" and row[2]==row[4] for row in rows)
        assert balance(db,part)==(2,2)
        assert balance(db,consumable)==(5,3)
        assert db.execute(
            "SELECT count(*) FROM auto_work_order_service WHERE work_order_id=%s",
            (order,),
        ).fetchone()[0]==1
        assert db.execute(
            """SELECT count(*) FROM notification_outbox
               WHERE aggregate_type='auto_appointment' AND aggregate_id=%s""",
            (appointment,),
        ).fetchone()[0]==1
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute(
                    """UPDATE auto_appointment_material_selection
                       SET quantity=1 WHERE work_order_id=%s""",(order,),
                )
        with pytest.raises(psycopg.errors.RaiseException,match="appointment material"):
            with db.transaction():
                db.execute(
                    """INSERT INTO auto_appointment_material_selection
                       (stock_event_id,work_order_id,sku_id,item_kind,quantity)
                       SELECT stock_event_id,work_order_id,sku_id,'PART',quantity+1
                       FROM auto_work_order_stock_event WHERE work_order_id=%s LIMIT 1""",
                    (order,),
                )
    assert http.post(create,json={**payload,"materialItems":[
        {**payload["materialItems"][0],"quantity":1},payload["materialItems"][1],
    ]},headers=idem).status_code==409
    # Другой ключ/время не может повторно зарезервировать исчерпанный SKU.
    later=datetime.fromisoformat(payload["startsAt"])+timedelta(hours=2)
    second=http.post(create,json={
        **payload,"startsAt":later.isoformat(),
        "endsAt":(later+timedelta(hours=1)).isoformat(),
        "materialItems":[{"skuId":str(part),"kind":"PART","quantity":1}],
    },headers={**headers,"Idempotency-Key":str(uuid4())})
    assert second.status_code==409,second.text
    assert second.json()["code"]=="STOCK_UNAVAILABLE"
    with psycopg.connect(DSN) as db:
        assert db.execute(
            "SELECT count(*) FROM auto_appointment WHERE organization_id=%s",(org,)
        ).fetchone()[0]==1
    with psycopg.connect(DSN) as db:
        db.execute("UPDATE auto_organization_role_grant SET revoked_at=now() WHERE id=%s",
                   (grant_id,))
    # Replay cannot bypass live WAREHOUSE revocation.
    assert http.post(create,json=payload,headers=idem).status_code==403


def test_failed_material_booking_has_no_phantom_appointment_or_stock(monkeypatch):
    d,org,loc,bay,http,part,consumable,foreign_sku,service,_=_ready(monkeypatch)
    headers=_auth(http,d,d["intake"])
    create=f"/api/v1/dev/auto/organizations/{org}/appointments"
    payload=_payload(d,org,loc,bay,service,part,consumable)
    for changed,expected in (
        ({"materialItems":[{"skuId":str(foreign_sku),"kind":"PART","quantity":1}]},403),
        ({"materialItems":[{"skuId":str(part),"kind":"PART","quantity":3}]},409),
        ({"materialItems":[{"skuId":str(part),"kind":"PART","quantity":1},
                           {"skuId":str(part),"kind":"CONSUMABLE","quantity":1}]},403),
        ({"materialItems":[{"skuId":str(part),"kind":"PART","quantity":0}]},422),
        ({"materialItems":[{"skuId":str(part),"kind":"UNKNOWN","quantity":1}]},422),
        ({"materialItems":[{"skuId":str(part),"kind":"PART","quantity":1}]*9},422),
    ):
        result=http.post(create,json={**payload,**changed},
                         headers={**headers,"Idempotency-Key":str(uuid4())})
        assert result.status_code==expected,(changed,result.text)
    with psycopg.connect(DSN) as db:
        assert db.execute(
            "SELECT count(*) FROM auto_appointment WHERE organization_id=%s",(org,)
        ).fetchone()[0]==0
        assert db.execute(
            """SELECT count(*) FROM auto_appointment_material_selection m
               JOIN auto_work_order w ON w.id=m.work_order_id
               WHERE w.organization_id=%s""",(org,)
        ).fetchone()[0]==0
        assert balance(db,part)==(2,0)
        assert balance(db,consumable)==(5,0)
    customer=_auth(http,d,d["client_a"])
    response=http.post(ROOT+"/appointments",json={
        "organizationId":str(org),"locationId":str(loc),
        "bayId":str(bay),"customerLinkId":str(d["link"]),
        "startsAt":payload["startsAt"],"endsAt":payload["endsAt"],
        "materialItems":payload["materialItems"],
    },headers={**customer,"Idempotency-Key":str(uuid4())})
    assert response.status_code==422,response.text


def test_missing_warehouse_role_and_released_material_receipt_fail_closed(monkeypatch):
    d,org,loc,bay,http,part,consumable,foreign_sku,service,_=_ready(monkeypatch,warehouse=False)
    headers=_auth(http,d,d["intake"])
    assert http.get(
        f"/api/v1/dev/auto/organizations/{org}/locations/{loc}/booking-stock"
    ).status_code==403
    payload=_payload(d,org,loc,bay,service,part,consumable)
    create=f"/api/v1/dev/auto/organizations/{org}/appointments"
    key={**headers,"Idempotency-Key":str(uuid4())}
    assert http.post(create,json=payload,headers=key).status_code==403
    with psycopg.connect(DSN) as db:
        grant(db,d["intake"],org,loc,"WAREHOUSE")
    created=http.post(create,json=payload,headers=key)
    assert created.status_code==201,created.text
    with psycopg.connect(DSN) as db:
        order=UUID(created.json()["workOrderId"])
        mutate_work_order_stock(
            db,d["tokens"][d["intake"]],org,loc,order,part,uuid4(),"RELEASE",1,
        )
        assert balance(db,part)==(2,1)
    # Старый receipt не выдаёт больше ложного 201 после RELEASE.
    assert http.post(create,json=payload,headers=key).status_code==409


def test_outer_transaction_failure_rolls_back_booking_stock_and_outbox(monkeypatch):
    d,org,loc,bay,http,part,consumable,foreign_sku,service,_=_ready(monkeypatch)
    token=d["tokens"][d["intake"]]
    start=datetime.now(timezone.utc)+timedelta(days=30)
    with psycopg.connect(DSN) as db:
        with pytest.raises(RuntimeError):
            with db.transaction():
                a,b,w=create_appointment(
                    db,token,org,loc,bay,d["link"],start,start+timedelta(hours=1),
                    request_key=uuid4(),service_ids=(service,),
                    material_items=((part,"PART",2),(consumable,"CONSUMABLE",3)),
                )
                assert db.execute(
                    "SELECT count(*) FROM auto_appointment_material_selection WHERE work_order_id=%s",
                    (w,),
                ).fetchone()[0]==2
                raise RuntimeError("synthetic rollback")
        assert balance(db,part)==(2,0)
        assert balance(db,consumable)==(5,0)
        assert db.execute(
            "SELECT count(*) FROM auto_appointment WHERE organization_id=%s",(org,)
        ).fetchone()[0]==0
        assert db.execute(
            "SELECT count(*) FROM auto_appointment_idempotency WHERE organization_id=%s",
            (org,),
        ).fetchone()[0]==0
        assert db.execute(
            """SELECT count(*) FROM notification_outbox WHERE aggregate_type='auto_appointment'
               AND aggregate_id IN (SELECT id FROM auto_appointment WHERE organization_id=%s)""",
            (org,),
        ).fetchone()[0]==0


def test_complete_calendar_directory_complex_move_history_and_cancel(monkeypatch):
    """Один сценарий: выбор ресурсов → комплекс → резерв → перенос → отмена."""
    from test_pg_auto_work_complexes_http import complex_body
    d,org,loc,bay,http,part,consumable,foreign_sku,service,_=_ready(monkeypatch)
    start=(datetime.now(timezone.utc)+timedelta(days=31)).replace(
        hour=0,minute=0,second=0,microsecond=0)
    another_bay=uuid4()
    extra=uuid4()
    with psycopg.connect(DSN) as db:
        manager_grant=grant(db,d["intake"],org,loc,"MANAGER")
        db.execute("""INSERT INTO auto_work_bay(id,organization_id,location_id,code)
                      VALUES(%s,%s,%s,'SYN-SECOND')""",(another_bay,org,loc))
        db.execute("""INSERT INTO auto_staff_shift(id,organization_id,location_id,user_id,starts_at,ends_at)
                      VALUES(%s,%s,%s,%s,%s,%s)""",
                   (uuid4(),org,loc,d["intake"],start+timedelta(hours=8),start+timedelta(hours=18)))
        db.execute("""INSERT INTO auto_service_catalog
            (id,organization_id,location_id,code,title,duration_minutes)
            VALUES(%s,%s,%s,'SYN-LONG','SYNTHETIC LONG WORK',75)""",(extra,org,loc))
        for i in range(101):
            db.execute("""INSERT INTO auto_service_catalog
                (id,organization_id,location_id,code,title,duration_minutes)
                VALUES(%s,%s,%s,%s,'SYNTHETIC SEARCH',30)""",
                (uuid4(),org,loc,f'SYN-SEARCH-{i:03d}'))
    root=f"/api/v1/dev/auto/organizations/{org}"
    branch=root+f"/locations/{loc}"
    directory=branch+"/booking-directory"
    assert http.get(directory,params={"kind":"bays"}).status_code==403
    headers=_auth(http,d,d["intake"])
    for kind,wanted in (("bays",bay),("vehicles",d["link"]),("employees",d["intake"]),("stock",part)):
        got=http.get(directory,params={"kind":kind})
        assert got.status_code==200,got.text
        assert got.headers["cache-control"]=="no-store"
        assert str(wanted) in [row["id"] for row in got.json()["items"]]
        assert all(set(row)=={"id","label","durationMinutes","available"} for row in got.json()["items"])
        assert str(foreign_sku) not in got.text
    assert http.get(directory,params=[("kind","bays"),("kind","vehicles")]).status_code==422
    assert http.get(directory,params={"kind":"services","search":"x"*65}).status_code==422
    seen=[]
    after=None
    for _ in range(3):
        params={"kind":"services","search":"SYN-SEARCH-"}
        if after:params["afterId"]=after
        got=http.get(directory,params=params)
        assert got.status_code==200,got.text
        seen.extend(x["id"] for x in got.json()["items"])
        after=got.json()["nextAfterId"]
    assert after is None and len(seen)==len(set(seen))==101
    payload=complex_body(service,part,consumable)
    payload["serviceIds"]=[str(service),str(extra)]
    defined=http.post(branch+"/work-complexes",headers=headers,json=payload)
    assert defined.status_code==201,defined.text
    ident=defined.json()["complex"]["id"]
    found=http.get(directory,params={"kind":"complexes","search":"SYN-COMPLEX"})
    assert found.status_code==200 and found.json()["items"]==[{
        "id":ident,"label":"SYN-COMPLEX · Synthetic repair package",
        "durationMinutes":105,"available":None}]
    slots_url=root+"/slot-candidates"
    slots_params={"locationId":str(loc),"bayId":str(bay),"day":start.date().isoformat(),
                  "employeeId":str(d["intake"]),"complexId":ident}
    slots=http.get(slots_url,params=slots_params)
    assert slots.status_code==200,slots.text
    assert slots.json()["items"]
    assert all(datetime.fromisoformat(x["endsAt"])-datetime.fromisoformat(x["startsAt"])
               ==timedelta(minutes=105) for x in slots.json()["items"])
    at=start+timedelta(hours=9)
    body={"locationId":str(loc),"bayId":str(bay),"customerLinkId":str(d["link"]),
          "employeeId":str(d["intake"]),"complexId":ident,"startsAt":at.isoformat(),
          "endsAt":(at+timedelta(minutes=105)).isoformat()}
    create=root+"/appointments"
    idem={**headers,"Idempotency-Key":str(uuid4())}
    assert http.post(create,json={**body,"serviceIds":[str(service)]},headers=idem).status_code==422
    short=http.post(create,json={**body,"endsAt":(at+timedelta(hours=1)).isoformat()},headers=idem)
    assert short.status_code==422 and short.json()["code"]=="SERVICES_EXCEED_SLOT"
    created=http.post(create,json=body,headers=idem)
    assert created.status_code==201,created.text
    receipt=created.json()
    assert http.post(create,json=body,headers=idem).json()==receipt
    order,appointment=receipt["workOrderId"],receipt["appointmentId"]
    with psycopg.connect(DSN) as db:
        assert db.execute("SELECT count(*) FROM auto_work_order_complex_selection WHERE work_order_id=%s",(order,)).fetchone()==(1,)
        assert db.execute("SELECT count(*) FROM auto_work_order_service WHERE work_order_id=%s",(order,)).fetchone()==(2,)
        assert balance(db,part)==(2,2)
        assert balance(db,consumable)==(5,1)
    later=start+timedelta(hours=12)
    moving={**headers,"Idempotency-Key":str(uuid4())}
    context={"locationId":str(loc),"bayId":str(another_bay),"day":start.date().isoformat(),
             "appointmentId":appointment}
    candidates=http.get(slots_url,params=context)
    assert candidates.status_code==200,candidates.text
    assert candidates.json()["items"]
    assert all(datetime.fromisoformat(x["endsAt"])-datetime.fromisoformat(x["startsAt"])
        ==timedelta(minutes=105) and datetime.fromisoformat(x["startsAt"])>=start+timedelta(hours=8)
        for x in candidates.json()["items"])
    assert http.get(slots_url,params={**context,"serviceId":str(service)}).status_code==403
    moved=http.post(create+"/"+appointment+"/reschedule",headers=moving,json={
        "bayId":str(another_bay),"startsAt":later.isoformat(),
        "endsAt":(later+timedelta(minutes=105)).isoformat()})
    assert moved.status_code==200,moved.text
    history_url=branch+"/appointments/"+appointment+"/history"
    history=http.get(history_url)
    assert history.status_code==200,history.text
    assert history.headers["cache-control"]=="no-store"
    assert history.json()["workOrderId"]==order
    assert history.json()["realDelivery"] is False
    assert [x["state"] for x in history.json()["items"]]==["CURRENT","REPLACED"]
    assert {x["action"]:x["notificationStatus"] for x in history.json()["items"]}=={
        "CREATED":"SUPERSEDED","RESCHEDULED":"PENDING"}
    customer_history=ROOT+"/appointments/"+appointment+"/history"
    customer_params={"organizationId":str(org),"locationId":str(loc)}
    _auth(http,d,d["client_b"])
    assert http.get(customer_history,params=customer_params).status_code==403
    _auth(http,d,d["client_a"])
    assert http.get(customer_history,params=customer_params).json()==history.json()
    customer_dir=http.get(ROOT+"/service-directory",params={
        **customer_params,"customerLinkId":str(d["link"]),"search":"SYN-SEARCH-"})
    assert customer_dir.status_code==200 and len(customer_dir.json()["items"])==50
    assert customer_dir.json()["nextAfterId"]
    assert http.get(ROOT+"/slots",params={**customer_params,"customerLinkId":str(d["link"]),
        "bayId":str(another_bay),"localDay":start.date().isoformat(),
        "appointmentId":appointment}).status_code==403  # Комплекс выбран сотрудником.
    headers=_auth(http,d,d["intake"])
    cancel_url=create+"/"+appointment+"/cancel"
    cancel_headers={**headers,"Idempotency-Key":str(uuid4())}
    assert http.post(cancel_url,headers=cancel_headers).status_code==409
    with psycopg.connect(DSN) as db:
        for sku_id,qty in ((part,2),(consumable,1)):
            mutate_work_order_stock(db,d["tokens"][d["intake"]],org,loc,UUID(order),
                                    sku_id,uuid4(),"RELEASE",qty)
    assert http.post(cancel_url,headers=cancel_headers).status_code==200
    cancelled=http.get(history_url).json()
    assert cancelled["items"][0]["state"]=="CANCELED"
    assert cancelled["cancellation"]["notificationStatus"]=="PENDING"
    assert {x["notificationStatus"] for x in cancelled["items"]}=={"SUPERSEDED"}
    with psycopg.connect(DSN) as db:
        assert balance(db,part)==(2,0) and balance(db,consumable)==(5,0)
        assert db.execute("SELECT count(*) FROM auto_appointment WHERE organization_id=%s",(org,)).fetchone()==(1,)
        db.execute("UPDATE auto_organization_role_grant SET revoked_at=now() WHERE id=%s",(manager_grant,))
    assert http.get(directory,params={"kind":"complexes"}).status_code==403
    with psycopg.connect(DSN) as db:
        db.execute("UPDATE auto_vehicle_customer_link SET revoked_at=now() WHERE id=%s",(d["link"],))
    assert http.get(history_url).status_code==403
    _auth(http,d,d["client_a"])
    assert http.get(customer_history,params=customer_params).status_code==403
