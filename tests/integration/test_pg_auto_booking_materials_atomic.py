"""PG17 + HTTPS: booking, services, PART/CONSUMABLE RESERVE in one transaction.

Only scoped synthetic stock, never payment, public access or real goods.
"""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

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
            "SELECT count(*) FROM auto_appointment_material_selection"
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
        order=created.json()["workOrderId"]
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
