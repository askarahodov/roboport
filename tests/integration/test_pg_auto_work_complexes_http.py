"""PG17 end-to-end: immutable service complexes and atomic SKU reservation in quotes."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import json
from uuid import uuid4

import psycopg
import pytest

from roboport_api.auto_work_complexes import select_complex,ComplexConflict
from test_pg_baseline import DSN
from test_pg_auto_work_order_estimate_http import scenario,api,token,path as estimate_path
from test_pg_auto_stock_ledger import sku,record,balance
from test_pg_auto_order_service_http import scenario as order_scenario


def setup(monkeypatch):
    d,org,loc,order,base_service,_,_=scenario()
    http=api(monkeypatch)
    with psycopg.connect(DSN) as db:
        extra=uuid4()
        db.execute("""INSERT INTO auto_service_catalog
          (id,organization_id,location_id,code,title,duration_minutes)
          VALUES(%s,%s,%s,'COMPLEX-SERVICE','SYNTHETIC COMPLEX REPAIR',35)
        """,(extra,org,loc))
        part,consumable=sku(db,org,loc),sku(db,org,loc)
        record(db,part,"RECEIPT",2)
        record(db,consumable,"RECEIPT",3)
    return d,org,loc,order,base_service,extra,part,consumable,http


def paths(org,loc,order):
    root=f"/api/v1/dev/auto/organizations/{org}/locations/{loc}"
    return root+"/work-complexes",root+f"/orders/{order}/complexes"


def complex_body(extra,part,consumable,key=None,code="SYN-COMPLEX"):
    return {"requestKey":str(key or uuid4()),"code":code,"title":"Synthetic repair package",
            "serviceIds":[str(extra)],"materials":[
              {"skuId":str(part),"itemKind":"PART","quantity":2,
               "unitPriceMinor":800},
              {"skuId":str(consumable),"itemKind":"CONSUMABLE","quantity":1,
               "unitPriceMinor":0}]}


def test_full_complex_catalog_atomic_stock_and_customer_material_quote(monkeypatch):
    d,org,loc,order,base_svc,extra,part,consumable,http=setup(monkeypatch)
    catalog,selected=paths(org,loc,order)
    assert http.get(catalog).status_code==403
    manager=token(http,d,d["intake"])
    assert http.get(catalog).json()["items"]==[]
    key=uuid4()
    payload=complex_body(extra,part,consumable,key=key)
    assert http.post(catalog,json=payload).status_code==403
    created=http.post(catalog,headers=manager,json=payload)
    assert created.status_code==201,created.text
    ident=created.json()["complex"]["id"]
    assert created.json()["complex"]["materials"][0]["itemKind"] in ("PART","CONSUMABLE")
    assert http.post(catalog,headers=manager,json=payload).json()["replayed"] is True
    assert http.post(catalog,headers=manager,json={
        **payload,"title":"Tampered"}).status_code==409
    assert http.get(catalog).json()["items"][0]["id"]==ident
    selection_key=uuid4()
    selected_body={"complexId":ident,"requestKey":str(selection_key)}
    assert http.post(selected,json=selected_body).status_code==403
    bound=http.post(selected,headers=manager,json=selected_body)
    assert bound.status_code==201,bound.text
    assert len(bound.json()["items"][0]["materialReceipts"])==2
    assert http.post(selected,headers=manager,json=selected_body).json()["replayed"] is True
    assert http.post(selected,headers=manager,json={
        "complexId":str(uuid4()),"requestKey":str(selection_key)}).status_code==409
    assert http.post(selected,headers=manager,json={
        "complexId":ident,"requestKey":str(uuid4())}).status_code==409
    with psycopg.connect(DSN) as db:
        assert balance(db,part)==(2,2)
        assert balance(db,consumable)==(3,1)
        assert db.execute("""SELECT count(*) FROM auto_work_order_service
          WHERE work_order_id=%s""",(order,)).fetchone()[0]==2
        assert db.execute("""SELECT count(*) FROM auto_work_order_stock_event
          WHERE work_order_id=%s AND action='RESERVE'""",(order,)).fetchone()[0]==2

    estimate=estimate_path(org,loc,order)
    quote=http.post(estimate,headers=manager,json={
        "requestKey":str(uuid4()),"expectedVersion":0,
        "prices":[{"serviceId":str(base_svc),"amountMinor":2000},
                  {"serviceId":str(extra),"amountMinor":3000}]})
    assert quote.status_code==201,quote.text
    row=quote.json()["items"][0]
    assert row["totalMinor"]==6600  # service 5000 + sold parts 1600; consumables 0
    assert len(row["materialLines"])==2
    assert {m["itemKind"] for m in row["materialLines"]}=={"PART","CONSUMABLE"}
    assert sum(m["amountMinor"] for m in row["materialLines"])==1600
    assert any(m["unitPriceMinor"]==0 and m["amountMinor"]==0
               for m in row["materialLines"])
    customer=token(http,d,d["client_a"])
    visible=http.get(estimate)
    assert visible.status_code==200
    assert visible.json()["items"][0]["materialLines"]==row["materialLines"]
    assert http.post(estimate+"/decision",headers=customer,json={
        "revisionId":row["id"],"requestKey":str(uuid4()),
        "decision":"APPROVED"}).status_code==201
    manager=token(http,d,d["intake"])
    assert http.post(selected,headers=manager,json=selected_body).status_code==200
    assert http.post(catalog,headers=manager,json={
        **complex_body(extra,part,consumable), "code":"SYN-COMPLEX-2"}).status_code==201
    with psycopg.connect(DSN) as db:
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("UPDATE auto_work_complex_material SET unit_price_minor=1 WHERE complex_id=%s",(ident,))
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("DELETE FROM auto_work_complex_material_reserved WHERE selection_id=%s",
                           (bound.json()["items"][0]["selectionId"],))
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("UPDATE auto_work_order_estimate_revision SET total_minor=1 WHERE id=%s",(row["id"],))


def test_complex_failed_reservation_rolls_back_all_services_and_receipts(monkeypatch):
    d,org,loc,order,base,extra,part,consumable,http=setup(monkeypatch)
    catalog,selected=paths(org,loc,order)
    manager=token(http,d,d["intake"])
    payload=complex_body(extra,part,consumable)
    payload["materials"][0]["quantity"]=3  # only two parts on hand
    created=http.post(catalog,headers=manager,json=payload)
    assert created.status_code==201,created.text
    chosen=http.post(selected,headers=manager,json={
        "requestKey":str(uuid4()),"complexId":created.json()["complex"]["id"]})
    assert chosen.status_code==409,chosen.text
    assert http.get(selected).json()["items"]==[]
    with psycopg.connect(DSN) as db:
        assert balance(db,part)==(2,0)
        assert balance(db,consumable)==(3,0)
        assert db.execute("""SELECT count(*) FROM auto_work_order_service
          WHERE work_order_id=%s""",(order,)).fetchone()==(1,)
        assert db.execute("""SELECT count(*) FROM auto_work_order_complex_selection
          WHERE work_order_id=%s""",(order,)).fetchone()==(0,)
        assert db.execute("""SELECT count(*) FROM auto_work_order_stock_event
          WHERE work_order_id=%s""",(order,)).fetchone()==(0,)


def test_pg_rejects_material_quote_omission_and_price_tamper(monkeypatch):
    d,org,loc,order,base,extra,part,consumable,http=setup(monkeypatch)
    catalog,selected=paths(org,loc,order)
    manager=token(http,d,d["intake"])
    created=http.post(catalog,headers=manager,
        json=complex_body(extra,part,consumable))
    assert created.status_code==201,created.text
    assert http.post(selected,headers=manager,json={
        "requestKey":str(uuid4()),"complexId":created.json()["complex"]["id"]
    }).status_code==201
    base_line={"serviceId":str(base),"code":"TEST-ORDER-SVC",
        "title":"SYNTHETIC INSPECTION","durationMinutes":45,"amountMinor":2000}
    extra_line={"serviceId":str(extra),"code":"COMPLEX-SERVICE",
        "title":"SYNTHETIC COMPLEX REPAIR","durationMinutes":35,"amountMinor":3000}
    with psycopg.connect(DSN) as db:
        for materials in ([],[{"skuId":str(part),"itemKind":"PART",
                                "quantity":2,"unitPriceMinor":200,"amountMinor":400}]):
            with pytest.raises(psycopg.errors.RaiseException):
                with db.transaction():
                    db.execute("""INSERT INTO auto_work_order_estimate_revision
                      (id,work_order_id,organization_id,location_id,version,actor_id,
                       request_key,fingerprint,total_minor,service_lines,
                       diagnostics_snapshot,material_lines)
                      VALUES(%s,%s,%s,%s,1,%s,%s,%s,6600,%s::jsonb,'[]'::jsonb,%s::jsonb)
                    """,(uuid4(),order,org,loc,d["intake"],uuid4(),"e"*64,
                         json.dumps([base_line,extra_line]),json.dumps(materials)))
        assert db.execute("SELECT count(*) FROM auto_work_order_estimate_revision WHERE work_order_id=%s",
                          (order,)).fetchone()==(0,)


def test_competing_complex_reservations_cannot_oversell(monkeypatch):
    d,org,loc,order,base,first_service,part,consumable,http=setup(monkeypatch)
    second_service=uuid4()
    with psycopg.connect(DSN) as db:
        db.execute("""INSERT INTO auto_service_catalog
          (id,organization_id,location_id,code,title,duration_minutes)
          VALUES(%s,%s,%s,'OTHER-COMPLEX','SYNTHETIC OTHER REPAIR',35)
        """,(second_service,org,loc))
    catalog,_=paths(org,loc,order)
    manager=token(http,d,d["intake"])
    first=http.post(catalog,headers=manager,json=complex_body(
        first_service,part,consumable,code="CMP-FIRST"))
    second=http.post(catalog,headers=manager,json=complex_body(
        second_service,part,consumable,code="CMP-SECOND"))
    assert first.status_code==201 and second.status_code==201
    ids=[first.json()["complex"]["id"],second.json()["complex"]["id"]]
    barrier=Barrier(2)
    def select(ident):
        barrier.wait(timeout=15)
        try:
            with psycopg.connect(DSN) as db:
                select_complex(db,d["tokens"][d["intake"]],org,loc,order,
                               request_key=uuid4(),complex_id=__import__('uuid').UUID(ident))
            return "RESERVED"
        except ComplexConflict:
            return "CONFLICT"
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(select,ids))==["CONFLICT","RESERVED"]
    with psycopg.connect(DSN) as db:
        assert balance(db,part)==(2,2)
        assert balance(db,consumable)==(3,1)
        assert db.execute("""SELECT count(*) FROM auto_work_order_complex_selection
          WHERE work_order_id=%s""",(order,)).fetchone()==(1,)
        assert db.execute("""SELECT count(*) FROM auto_work_order_stock_event
          WHERE work_order_id=%s AND action='RESERVE'""",(order,)).fetchone()==(2,)
