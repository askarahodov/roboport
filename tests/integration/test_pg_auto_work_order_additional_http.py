"""PG17 + scoped HTTPS: additional labor needs separate, immutable client consent."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import UUID,uuid4
import json

import psycopg
import pytest
from fastapi.testclient import TestClient
from roboport_api.main import app
from roboport_api.auto_work_order_additional import (
    publish_extra,decide_extra,ExtraConflict,
)
from test_pg_baseline import DSN
from test_pg_auto_work_order_execution_http import fixture,session,post,url


def setup(monkeypatch):
    d,org,loc,order,service,_,_=fixture(monkeypatch,with_material=False)
    additional=uuid4()
    with psycopg.connect(DSN) as db:
        db.execute("""INSERT INTO auto_service_catalog
          (id,organization_id,location_id,code,title,duration_minutes)
          VALUES(%s,%s,%s,'SYN-ADDITIONAL','ADDITIONAL SYNTHETIC REPAIR',30)
        """,(additional,org,loc))
        base=db.execute("""SELECT r.id FROM auto_work_order_estimate_revision r
          WHERE r.work_order_id=%s""",(order,)).fetchone()[0]
    return d,org,loc,order,service,additional,base


def quote(service,base,version=0,key=None,discount=1000,rate=10001):
    return {"baseRevisionId":str(base),"expectedVersion":version,
            "requestKey":str(key or uuid4()),
            "workRates":[{"serviceId":str(service),"qualificationCode":"MASTER",
              "rateMinorPerHour":rate,"quantity":2,"coefficientBps":12500,
              "discountBps":discount}]}


def extra_path(org,loc,order):
    return url(org,loc,order).removesuffix("/execution")+"/extras"


def test_additional_work_requires_new_client_consent_before_labor_and_qc(monkeypatch):
    d,org,loc,order,base_service,additional,base=setup(monkeypatch)
    http=TestClient(app,base_url="https://127.0.0.1")
    endpoint=extra_path(org,loc,order)
    execution=url(org,loc,order)
    manager=session(http,d,d["intake"])
    assert post(http,execution+"/start",manager).status_code==201
    mech=session(http,d,d["manager"])
    assert post(http,execution+"/labor",mech,serviceId=str(additional),
                actualMinutes=25).status_code==409
    assert post(http,execution+"/labor",mech,serviceId=str(base_service),
                actualMinutes=35).status_code==201
    manager=session(http,d,d["intake"])
    payload=quote(additional,base,key=uuid4())
    published=http.post(endpoint,headers=manager,json=payload)
    assert published.status_code==201,published.text
    version=published.json()["items"][0]
    assert version["totalMinor"]==11251  # 30m × 10001 × 2 × 1.25 × 0.9
    assert version["lines"][0]["serviceId"]==str(additional)
    assert published.json()["releaseAllowed"] is False
    replay=http.post(endpoint,headers=manager,json=payload)
    assert replay.status_code==200 and replay.json()["replayed"] is True
    mech=session(http,d,d["manager"])
    assert post(http,execution+"/labor",mech,serviceId=str(additional),
                actualMinutes=25).status_code==409
    assert post(http,execution+"/finish",mech).status_code==409
    customer=session(http,d,d["client_a"])
    visible=http.get(endpoint)
    assert visible.status_code==200 and visible.json()["items"][0]["digest"]==version["digest"]
    decision={"revisionId":version["id"],"requestKey":str(uuid4()),"decision":"APPROVED"}
    assert http.post(endpoint+"/decision",json=decision).status_code==403
    accepted=http.post(endpoint+"/decision",headers=customer,json=decision)
    assert accepted.status_code==201,accepted.text
    assert http.post(endpoint+"/decision",headers=customer,json=decision).status_code==200
    mech=session(http,d,d["manager"])
    line=post(http,execution+"/labor",mech,serviceId=str(additional),
              actualMinutes=25)
    assert line.status_code==201,line.text
    assert line.json()["approvedExtraVersion"]==1
    assert line.json()["approvedExtraTotalMinor"]==11251
    assert {x["serviceId"] for x in line.json()["requiredServices"]}=={
        str(base_service),str(additional)}
    finished=post(http,execution+"/finish",mech)
    assert finished.status_code==201,finished.text
    assert finished.json()["status"]=="READY_FOR_QC"
    assert http.post(endpoint,headers=manager,json=quote(additional,base,version=1)
                     ).status_code in (403,409)  # currently mechanic cookie; no manager access
    qc_endpoint=execution.removesuffix("/execution")+"/qc"
    independent=session(http,d,d["other"])
    qc=post(http,qc_endpoint,independent,result="PASS",findingCode="NONE")
    assert qc.status_code==201,qc.text
    assert qc.json()["status"]=="QC_PASSED"
    with psycopg.connect(DSN) as db:
        assert db.execute("""SELECT count(*) FROM auto_work_order_labor_entry labor
          JOIN auto_work_order_execution e ON e.id=labor.execution_id
          WHERE e.work_order_id=%s""",(order,)).fetchone()[0]==2
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("""UPDATE auto_work_order_extra_revision
                  SET total_minor=1 WHERE id=%s""",(version["id"],))


def test_additional_work_scope_replay_mutability_and_no_silent_approval(monkeypatch):
    d,org,loc,order,base_service,additional,base=setup(monkeypatch)
    http=TestClient(app,base_url="https://127.0.0.1")
    endpoint=extra_path(org,loc,order)
    manager=session(http,d,d["intake"])
    assert http.post(endpoint,json=quote(additional,base)).status_code==403
    assert http.get(endpoint).status_code==200
    assert http.post(endpoint,headers=manager,json=quote(base_service,base)).status_code==409
    assert http.post(endpoint,headers=manager,json=quote(uuid4(),base)).status_code==409
    assert http.post(endpoint.replace(str(loc),str(uuid4())),
                     headers=manager,json=quote(additional,base)).status_code==403
    assert http.post(endpoint,headers=manager,json=quote(
        additional,base,rate=-1)).status_code==422
    invalid=quote(additional,base)
    invalid["workRates"].append(invalid["workRates"][0])
    assert http.post(endpoint,headers=manager,json=invalid).status_code==422
    key=uuid4()
    body=quote(additional,base,key=key)
    created=http.post(endpoint,headers=manager,json=body)
    assert created.status_code==201,created.text
    assert http.post(endpoint,headers=manager,json=quote(
        additional,base,key=key,rate=10002)).status_code==409
    first=created.json()["items"][0]
    customer=session(http,d,d["client_a"])
    declined=http.post(endpoint+"/decision",headers=customer,json={
        "revisionId":first["id"],"requestKey":str(uuid4()),"decision":"DECLINED"})
    assert declined.status_code==201,declined.text
    mech=session(http,d,d["manager"])
    assert post(http,url(org,loc,order)+"/start",session(http,d,d["intake"])).status_code==201
    mech=session(http,d,d["manager"])
    assert post(http,url(org,loc,order)+"/labor",mech,
                serviceId=str(additional),actualMinutes=12).status_code==409
    manager=session(http,d,d["intake"])
    second=http.post(endpoint,headers=manager,json=quote(additional,base,version=1))
    assert second.status_code==201,second.text
    assert second.json()["items"][0]["decision"]=="DECLINED"
    assert second.json()["items"][1]["decision"] is None
    customer=session(http,d,d["client_a"])
    assert http.post(endpoint+"/decision",headers=customer,json={
        "revisionId":first["id"],"requestKey":str(uuid4()),
        "decision":"APPROVED"}).status_code==409
    assert http.get(endpoint.replace(str(org),str(uuid4()))).status_code==403
    with psycopg.connect(DSN) as db:
        db.execute("UPDATE auto_vehicle_customer_link SET revoked_at=now() WHERE id=%s",
                   (d["link"],))
    assert http.get(endpoint).status_code==403


def test_postgres_refuses_forged_additional_price_and_missing_consent(monkeypatch):
    d,org,loc,order,base_service,additional,base=setup(monkeypatch)
    good={"serviceId":str(additional),"code":"SYN-ADDITIONAL",
        "title":"ADDITIONAL SYNTHETIC REPAIR","durationMinutes":30,
        "pricingMethod":"NORM_HOUR_FACTORED","qualificationCode":"MASTER",
        "rateMinorPerHour":10001,"quantity":2,
        "coefficientBps":12500,"discountBps":1000,"amountMinor":11251}
    with psycopg.connect(DSN) as db:
        for bad in ({**good,"amountMinor":11250},{**good,"discountBps":2501},
                    {**good,"serviceId":str(base_service)},
                    {**good,"serviceId":str(uuid4())},
                    {**good,"durationMinutes":31}):
            with pytest.raises(psycopg.errors.RaiseException):
                with db.transaction():
                    db.execute("""INSERT INTO auto_work_order_extra_revision
                      (id,work_order_id,organization_id,location_id,base_revision_id,
                       version,actor_id,request_key,fingerprint,total_minor,service_lines)
                      VALUES(%s,%s,%s,%s,%s,1,%s,%s,%s,11251,%s::jsonb)
                    """,(uuid4(),order,org,loc,base,d["intake"],uuid4(),
                        "e"*64,json.dumps([bad])))
        assert db.execute("""SELECT count(*) FROM auto_work_order_extra_revision
          WHERE work_order_id=%s""",(order,)).fetchone()[0]==0


def test_concurrent_extra_publications_and_consent_serialize(monkeypatch):
    d,org,loc,order,_,additional,base=setup(monkeypatch)
    key=uuid4()
    rate_input=quote(additional,base)["workRates"][0]
    rate_input["serviceId"]=additional
    barrier=Barrier(2)
    def publish(_):
        barrier.wait(timeout=15)
        try:
            with psycopg.connect(DSN) as db:
                publish_extra(db,d["tokens"][d["intake"]],org,loc,order,
                    base_revision_id=base,request_key=uuid4(),expected_version=0,
                    work_rates=[rate_input])
            return "CREATED"
        except ExtraConflict:
            return "CONFLICT"
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(publish,[0,1]))==["CONFLICT","CREATED"]
    with psycopg.connect(DSN) as db:
        revision=db.execute("""SELECT id FROM auto_work_order_extra_revision
          WHERE work_order_id=%s""",(order,)).fetchone()[0]
    barrier=Barrier(2)
    def decide(decision):
        barrier.wait(timeout=15)
        try:
            with psycopg.connect(DSN) as db:
                decide_extra(db,d["tokens"][d["client_a"]],org,loc,order,
                    revision_id=revision,request_key=uuid4(),decision=decision)
            return "CREATED"
        except ExtraConflict:
            return "CONFLICT"
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(decide,["APPROVED","DECLINED"]))==["CONFLICT","CREATED"]
    with psycopg.connect(DSN) as db:
        assert db.execute("""SELECT count(*) FROM auto_work_order_extra_decision
          WHERE work_order_id=%s""",(order,)).fetchone()[0]==1
