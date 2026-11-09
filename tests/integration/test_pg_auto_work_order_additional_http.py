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


@pytest.mark.parametrize("last_decision",["APPROVED","DECLINED"])
def test_superseded_pending_extra_does_not_block_finish(monkeypatch,last_decision):
    """The first pending revision is historical after a newer client decision."""
    d,org,loc,order,base_service,additional,base=setup(monkeypatch)
    http=TestClient(app,base_url="https://127.0.0.1")
    endpoint=extra_path(org,loc,order)
    execution=url(org,loc,order)
    manager=session(http,d,d["intake"])
    first=http.post(endpoint,headers=manager,
                    json=quote(additional,base,version=0,discount=1000))
    assert first.status_code==201,first.text
    assert first.json()["items"][0]["decision"] is None
    second=http.post(endpoint,headers=manager,
                     json=quote(additional,base,version=1,discount=500))
    assert second.status_code==201,second.text
    latest=second.json()["items"][-1]
    assert latest["version"]==2 and latest["decision"] is None

    client=session(http,d,d["client_a"])
    decision=http.post(endpoint+"/decision",headers=client,json={
        "revisionId":latest["id"],"requestKey":str(uuid4()),
        "decision":last_decision,
    })
    assert decision.status_code==201,decision.text
    assert decision.json()["items"][0]["decision"] is None
    assert decision.json()["items"][-1]["decision"]==last_decision

    manager=session(http,d,d["intake"])
    assert post(http,execution+"/start",manager).status_code==201
    mechanic=session(http,d,d["manager"])
    assert post(http,execution+"/labor",mechanic,
                serviceId=str(base_service),actualMinutes=20).status_code==201
    if last_decision=="APPROVED":
        extra_labor=post(http,execution+"/labor",mechanic,
                         serviceId=str(additional),actualMinutes=18)
        assert extra_labor.status_code==201,extra_labor.text
    else:
        assert post(http,execution+"/labor",mechanic,
                    serviceId=str(additional),actualMinutes=18).status_code==409
    finished=post(http,execution+"/finish",mechanic)
    assert finished.status_code==201,finished.text
    assert finished.json()["status"]=="READY_FOR_QC"
    with psycopg.connect(DSN) as db:
        assert db.execute("""
          SELECT count(*) FROM auto_work_order_extra_revision r
          LEFT JOIN auto_work_order_extra_decision d ON d.revision_id=r.id
          WHERE r.work_order_id=%s AND d.id IS NULL
        """,(order,)).fetchone()[0]==1



def test_fifty_base_plus_twenty_approved_extra_services_can_finish(monkeypatch):
    """No 50-labor truncation when an approved 20-line supplement brings 70."""
    from test_pg_auto_work_order_estimate_http import scenario as base_scenario
    from test_pg_auto_stock_service import grant
    from roboport_api.auto_order_service_link import add_synthetic_order_service
    from roboport_api.auto_workshop_qc import submit_work_order_inspection
    from roboport_api.auto_work_order_estimate import (
        publish_auto_estimate,decide_auto_estimate,
    )
    from roboport_api.auto_work_order_execution import (
        start_execution,labor_execution,
    )
    d,org,loc,order,initial,_,_=base_scenario()
    http=TestClient(app,base_url="https://127.0.0.1")
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTO_API","1")
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTH_API","1")
    monkeypatch.setenv("ROBOport_DATABASE_URL",DSN)
    base_services=[initial]
    extras=[]
    with psycopg.connect(DSN) as db:
        grant(db,d["manager"],org,loc,"MECHANIC")
        for number in range(49):
            svc=uuid4()
            db.execute("""INSERT INTO auto_service_catalog
              (id,organization_id,location_id,code,title,duration_minutes)
              VALUES(%s,%s,%s,%s,%s,15)""",
              (svc,org,loc,f"BASE-50-{number:02d}",f"BASE SERVICE {number:02d}"))
            add_synthetic_order_service(db,d["tokens"][d["intake"]],
                organization_id=org,location_id=loc,work_order_id=order,
                service_id=svc,synthetic_only=True)
            base_services.append(svc)
        for number in range(20):
            svc=uuid4()
            db.execute("""INSERT INTO auto_service_catalog
              (id,organization_id,location_id,code,title,duration_minutes)
              VALUES(%s,%s,%s,%s,%s,15)""",
              (svc,org,loc,f"EXTRA-20-{number:02d}",f"EXTRA SERVICE {number:02d}"))
            extras.append(svc)
        submit_work_order_inspection(db,d["tokens"][d["intake"]],org,loc,order,
            odometer_km=9000,fuel_percent=50,tyres_ok=True,lights_ok=True,
            fluid_leak=False,exterior_code="CLEAR")
        estimate=publish_auto_estimate(db,d["tokens"][d["intake"]],org,loc,order,
            request_key=uuid4(),expected_version=0,
            prices=[{"serviceId":svc,"amountMinor":200} for svc in base_services])
        revision=UUID(estimate["items"][0]["id"])
        decide_auto_estimate(db,d["tokens"][d["client_a"]],org,loc,order,
            revision_id=revision,request_key=uuid4(),decision="APPROVED")
        extra=publish_extra(db,d["tokens"][d["intake"]],org,loc,order,
            base_revision_id=revision,request_key=uuid4(),expected_version=0,
            work_rates=[{"serviceId":svc,"qualificationCode":"MASTER",
              "rateMinorPerHour":10001,"quantity":1,
              "coefficientBps":10000,"discountBps":0} for svc in extras])
        decide_extra(db,d["tokens"][d["client_a"]],org,loc,order,
            revision_id=UUID(extra["items"][0]["id"]),
            request_key=uuid4(),decision="APPROVED")
        started=start_execution(db,d["tokens"][d["intake"]],org,loc,order,uuid4())
        assert len(started["requiredServices"])==70
        for svc in base_services+extras[:-1]:
            labor_execution(db,d["tokens"][d["manager"]],org,loc,order,
                svc,20,uuid4())
    # The 70th POST must commit and respond with all 70 labor lines.
    mechanic=session(http,d,d["manager"])
    executed=post(http,url(org,loc,order)+"/labor",mechanic,
                  serviceId=str(extras[-1]),actualMinutes=20)
    assert executed.status_code==201,executed.text
    assert len(executed.json()["labor"])==70
    assert len(executed.json()["requiredServices"])==70
    finished=post(http,url(org,loc,order)+"/finish",mechanic)
    assert finished.status_code==201,finished.text
    assert finished.json()["status"]=="READY_FOR_QC"
    with psycopg.connect(DSN) as db:
        assert db.execute("""SELECT count(*) FROM auto_work_order_labor_entry labor
          JOIN auto_work_order_execution e ON e.id=labor.execution_id
          WHERE e.work_order_id=%s""",(order,)).fetchone()[0]==70


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
