"""PG17 HTTPS: exact norm-hour price formula → immutable quote → customer consent.

No real hourly rate registry, mechanic certification, wage or fiscal ledger.
"""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import json
from uuid import uuid4

import psycopg
import pytest

from roboport_api.auto_work_order_estimate import (
    publish_auto_estimate, AutoEstimateConflict,
)
from test_pg_auto_work_order_estimate_http import scenario,api,token,path as estimate_path
from test_pg_baseline import DSN


def rated(service,rate=10001,category="DIAGNOSTIC",key=None,version=0):
    return {"requestKey":str(key or uuid4()),"expectedVersion":version,
            "laborRates":[{"serviceId":str(service),
                "qualificationCode":category,"rateMinorPerHour":rate}]}


def test_norm_hour_complete_customer_flow_math_version_replay_and_scope(monkeypatch):
    d,org,loc,order,service,foreign,_=scenario()
    client=api(monkeypatch)
    quote=estimate_path(org,loc,order)
    manager=token(client,d,d["intake"])
    key=uuid4()
    body=rated(service,key=key)
    assert client.post(quote,json=body).status_code==403
    # Invalid body fails before mutation, including ambiguous pricing modes.
    assert client.post(quote,headers=manager,json={
        **body,"prices":[{"serviceId":str(service),"amountMinor":100}]
    }).status_code==422
    assert client.post(quote,headers=manager,json={
        "requestKey":str(uuid4()),"expectedVersion":0
    }).status_code==422
    for bad in (-1,10000000001,1.5,"10001",True):
        assert client.post(quote,headers=manager,json=rated(service,rate=bad)).status_code==422
    assert client.post(quote,headers=manager,json=rated(
        service,category="FAKE_CERTIFICATION")).status_code==422
    assert client.post(quote,headers=manager,json=rated(foreign)).status_code==409
    duplicated=rated(service)
    duplicated["laborRates"].append(duplicated["laborRates"][0])
    assert client.post(quote,headers=manager,json=duplicated).status_code==422
    assert client.get(quote).json()["items"]==[]
    # 45 min × 10001 kop/h = 7500.75 → 7501 (half-up).
    published=client.post(quote,headers=manager,json=body)
    assert published.status_code==201,published.text
    current=published.json()["items"][0]
    assert current["totalMinor"]==7501
    assert current["lines"]==[{
        "serviceId":str(service),"code":"TEST-ORDER-SVC",
        "title":"SYNTHETIC INSPECTION","durationMinutes":45,
        "pricingMethod":"NORM_HOUR","qualificationCode":"DIAGNOSTIC",
        "rateMinorPerHour":10001,"amountMinor":7501,
    }]
    assert len(current["digest"])==64
    assert published.json()["releaseAllowed"] is False
    assert client.post(quote,headers=manager,json=body).json()["replayed"] is True
    assert client.post(quote,headers=manager,json={
        **body,"laborRates":[{**body["laborRates"][0],"rateMinorPerHour":10002}]
    }).status_code==409
    assert client.post(quote,headers=manager,json=rated(service,version=0)
                       ).status_code==409
    # The client sees the same frozen tariff/duration in their exact revision.
    customer=token(client,d,d["client_a"])
    own=client.get(quote)
    assert own.status_code==200,own.text
    assert own.headers["cache-control"]=="no-store"
    assert own.json()["items"][0]["lines"]==current["lines"]
    approval={"revisionId":current["id"],"requestKey":str(uuid4()),
              "decision":"APPROVED"}
    assert client.post(quote+"/decision",json=approval).status_code==403
    accepted=client.post(quote+"/decision",headers=customer,json=approval)
    assert accepted.status_code==201,accepted.text
    assert accepted.json()["items"][0]["digest"]==current["digest"]
    assert accepted.json()["items"][0]["lines"]==current["lines"]
    manager=token(client,d,d["intake"])
    assert client.post(quote,headers=manager,json=body).status_code==200
    assert client.post(quote,headers=manager,json=rated(service,version=1)
                       ).status_code==409
    with psycopg.connect(DSN) as db:
        assert db.execute("""
          SELECT count(*) FROM auto_work_order_estimate_revision
          WHERE work_order_id=%s""",(order,)).fetchone()==(1,)
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("""UPDATE auto_work_order_estimate_revision
                   SET total_minor=1 WHERE id=%s""",(current["id"],))
    token(client,d,d["client_b"])
    assert client.get(quote).status_code==403
    assert client.get(quote.replace(str(loc),str(uuid4()))).status_code==403
    with psycopg.connect(DSN) as db:
        db.execute("UPDATE auto_vehicle_customer_link SET revoked_at=now() WHERE id=%s",
                   (d["link"],))
    manager=token(client,d,d["intake"])
    assert client.post(quote,headers=manager,json=body).status_code==403
    customer=token(client,d,d["client_a"])
    assert client.get(quote).status_code==403


def test_postgres_refuses_forged_norm_hour_amount_even_with_matching_services(monkeypatch):
    d,org,loc,order,service,_,_=scenario()
    api(monkeypatch)
    # A direct SQL writer must not bypass the source duration, formula or total.
    correct={"serviceId":str(service),"code":"TEST-ORDER-SVC",
             "title":"SYNTHETIC INSPECTION","durationMinutes":45,
             "pricingMethod":"NORM_HOUR","qualificationCode":"GENERAL",
             "rateMinorPerHour":10001,"amountMinor":7501}
    with psycopg.connect(DSN) as db:
        for forged in [
            {**correct,"amountMinor":7500},
            {**correct,"durationMinutes":46},
            {**correct,"qualificationCode":"CERTIFIED"},
            {**correct,"rateMinorPerHour":10000000001},
            {**correct,"serviceId":str(uuid4())},
            {**correct,"pricingMethod":"MANUAL"},
        ]:
            with pytest.raises(psycopg.errors.RaiseException):
                with db.transaction():
                    db.execute("""INSERT INTO auto_work_order_estimate_revision
                    (id,work_order_id,organization_id,location_id,version,actor_id,
                     request_key,fingerprint,total_minor,service_lines,
                     diagnostics_snapshot)
                    VALUES(%s,%s,%s,%s,1,%s,%s,%s,%s,%s::jsonb,'[]'::jsonb)
                    """,(uuid4(),order,org,loc,d["intake"],uuid4(),"a"*64,
                          7501,json.dumps([forged])))
        # The DB enforces the total, not just individual line arithmetic.
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("""INSERT INTO auto_work_order_estimate_revision
                (id,work_order_id,organization_id,location_id,version,actor_id,
                 request_key,fingerprint,total_minor,service_lines,
                 diagnostics_snapshot)
                VALUES(%s,%s,%s,%s,1,%s,%s,%s,%s,%s::jsonb,'[]'::jsonb)
                """,(uuid4(),order,org,loc,d["intake"],uuid4(),"b"*64,7500,
                      json.dumps([correct])))
        assert db.execute(
            "SELECT count(*) FROM auto_work_order_estimate_revision WHERE work_order_id=%s",
            (order,),
        ).fetchone()==(0,)


def test_competing_norm_hour_publications_and_new_version_are_exact(monkeypatch):
    d,org,loc,order,service,_,_=scenario()
    api(monkeypatch)
    barrier=Barrier(2)
    def publish(rate):
        barrier.wait(timeout=15)
        try:
            with psycopg.connect(DSN) as db:
                state=publish_auto_estimate(
                    db,d["tokens"][d["intake"]],org,loc,order,
                    request_key=uuid4(),expected_version=0,
                    labor_rates=[{"serviceId":service,
                                  "qualificationCode":"GENERAL",
                                  "rateMinorPerHour":rate}])
            return state["items"][0]["totalMinor"]
        except AutoEstimateConflict:
            return "CONFLICT"
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(publish,[10001,20001]))
    assert results.count("CONFLICT")==1
    assert [x for x in results if isinstance(x,int)][0] in (7501,15001)
    http=api(monkeypatch)
    manager=token(http,d,d["intake"])
    quoted=http.get(estimate_path(org,loc,order)).json()["items"]
    assert len(quoted)==1 and quoted[0]["version"]==1
    update=rated(service,rate=30001,version=1,category="MASTER")
    second=http.post(estimate_path(org,loc,order),headers=manager,json=update)
    assert second.status_code==201,second.text
    assert second.json()["items"][-1]["lines"][0]["amountMinor"]==22501
    assert second.json()["items"][0]["lines"]==quoted[0]["lines"]
    assert second.json()["items"][0]["digest"]==quoted[0]["digest"]
