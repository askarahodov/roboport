"""PG17 + HTTPS: immutable automotive estimate revisions and exact customer consent."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4
import psycopg
import pytest
from fastapi.testclient import TestClient
from roboport_api.main import app
from roboport_api.auto_order_service_link import add_synthetic_order_service
from roboport_api.auto_work_order_estimate import (
    AutoEstimateConflict, publish_auto_estimate, decide_auto_estimate,
)
from test_pg_baseline import DSN
from test_pg_auto_order_service_http import scenario as order_scenario


def scenario():
    d,org,loc,bay,order,appt,start,service,foreign,grant=order_scenario()
    with psycopg.connect(DSN) as db:
        add_synthetic_order_service(
            db,d["tokens"][d["intake"]],organization_id=org,
            location_id=loc,work_order_id=order,service_id=service,synthetic_only=True)
    return d,org,loc,order,service,foreign,grant


def api(monkeypatch):
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTO_API","1")
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTH_API","1")
    monkeypatch.setenv("ROBOport_DATABASE_URL",DSN)
    return TestClient(app,base_url="https://127.0.0.1")


def token(client,d,actor):
    client.cookies.set("rp_session",d["tokens"][actor])
    return {"Origin":"https://127.0.0.1","X-CSRF-Token":d["csrf"][actor]}


def path(org,loc,order):
    return f"/api/v1/dev/auto/organizations/{org}/locations/{loc}/orders/{order}/estimates"


def publish_payload(service,version=0,amount=9500,key=None):
    return {"requestKey":str(key or uuid4()),"expectedVersion":version,
            "prices":[{"serviceId":str(service),"amountMinor":amount}]}


def test_manager_publish_customer_exact_latest_approval_and_freeze(monkeypatch):
    d,org,loc,order,service,foreign,grant=scenario()
    client=api(monkeypatch)
    url=path(org,loc,order)
    assert client.get(url).status_code==403
    assert client.post(url,json=publish_payload(service)).status_code==403
    manager=token(client,d,d["intake"])
    assert client.get(url).json()["items"]==[]
    key=uuid4()
    body=publish_payload(service,key=key)
    assert client.post(url,json=body).status_code==403
    assert client.post(url,headers=manager,json={
        **body,"prices":[{"serviceId":str(foreign),"amountMinor":5}]
    }).status_code==409
    assert client.post(url,headers=manager,json={
        **body,"prices":[{"serviceId":str(service),"amountMinor":-1}]
    }).status_code==422
    created=client.post(url,headers=manager,json=body)
    assert created.status_code==201,created.text
    row=created.json()["items"][0]
    assert row["version"]==1 and row["totalMinor"]==9500
    assert row["lines"][0]["code"]=="TEST-ORDER-SVC"
    assert created.json()["syntheticOnly"] is True
    assert created.json()["releaseAllowed"] is False
    assert len(row["digest"])==64
    same=client.post(url,headers=manager,json=body)
    assert same.status_code==200 and same.json()["replayed"] is True
    assert client.post(url,headers=manager,json=publish_payload(service,amount=500,
                     key=key)).status_code==409
    assert client.post(url,headers=manager,json=publish_payload(
        service,version=0)).status_code==409
    # New lines cannot be attached to the WorkOrder after quote publication.
    with psycopg.connect(DSN) as db:
        extra=uuid4()
        db.execute("""INSERT INTO auto_service_catalog
          (id,organization_id,location_id,code,title,duration_minutes)
          VALUES (%s,%s,%s,'SECOND-SYN','SYNTHETIC SECOND',20)""",(extra,org,loc))
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("""INSERT INTO auto_work_order_service
                  (id,work_order_id,service_id,organization_id,location_id)
                  VALUES(%s,%s,%s,%s,%s)""",(uuid4(),order,extra,org,loc))
    client_user=token(client,d,d["client_b"])
    assert client.get(url).status_code==403
    decision_url=url+"/decision"
    assert client.post(decision_url,headers=client_user,json={
        "revisionId":row["id"],"requestKey":str(uuid4()),"decision":"APPROVED"
    }).status_code==403
    customer=token(client,d,d["client_a"])
    assert client.get(url).status_code==200
    receipt_key=uuid4()
    consent={"revisionId":row["id"],"requestKey":str(receipt_key),
             "decision":"APPROVED"}
    assert client.post(decision_url,json=consent).status_code==403
    approved=client.post(decision_url,headers=customer,json=consent)
    assert approved.status_code==201,approved.text
    assert approved.json()["items"][0]["decision"]=="APPROVED"
    assert approved.json()["releaseAllowed"] is False
    assert client.post(decision_url,headers=customer,json=consent).status_code==200
    assert client.post(decision_url,headers=customer,json={
        **consent,"decision":"DECLINED"}).status_code==409
    manager=token(client,d,d["intake"])
    assert client.post(url,headers=manager,json=publish_payload(service,version=1)
                       ).status_code==409
    with psycopg.connect(DSN) as db:
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("""DELETE FROM auto_work_order_estimate_decision
                              WHERE revision_id=%s""",(row["id"],))
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("""UPDATE auto_work_order_estimate_revision
                              SET total_minor=3 WHERE id=%s""",(row["id"],))


def test_supersede_unanswered_version_decline_and_revoke_proof(monkeypatch):
    d,org,loc,order,service,_,grant=scenario()
    client=api(monkeypatch)
    url=path(org,loc,order)
    manager=token(client,d,d["intake"])
    first=client.post(url,headers=manager,json=publish_payload(service,amount=1500))
    assert first.status_code==201,first.text
    first_id=first.json()["items"][0]["id"]
    second=client.post(url,headers=manager,json=publish_payload(
        service,version=1,amount=2700))
    assert second.status_code==201,second.text
    assert second.json()["latestVersion"]==2
    latest_id=second.json()["items"][-1]["id"]
    customer=token(client,d,d["client_a"])
    assert client.post(url+"/decision",headers=customer,json={
        "revisionId":first_id,"requestKey":str(uuid4()),
        "decision":"APPROVED"}).status_code==409
    declined=client.post(url+"/decision",headers=customer,json={
        "revisionId":latest_id,"requestKey":str(uuid4()),
        "decision":"DECLINED"})
    assert declined.status_code==201,declined.text
    assert declined.json()["items"][-1]["decision"]=="DECLINED"
    with psycopg.connect(DSN) as db:
        db.execute("UPDATE auto_vehicle_customer_link SET revoked_at=now() WHERE id=%s",
                   (d["link"],))
    assert client.get(url).status_code==403
    assert client.post(url+"/decision",headers=customer,json={
        "revisionId":latest_id,"requestKey":str(uuid4()),
        "decision":"DECLINED"}).status_code==403
    manager=token(client,d,d["intake"])
    assert client.get(url).status_code==403


def test_concurrent_publish_and_concurrent_customer_consent(monkeypatch):
    d,org,loc,order,service,_,grant=scenario()
    api(monkeypatch)
    publish_barrier=Barrier(2)
    def publish(_):
        publish_barrier.wait(timeout=10)
        try:
            with psycopg.connect(DSN) as db:
                publish_auto_estimate(
                    db,d["tokens"][d["intake"]],org,loc,order,
                    request_key=uuid4(),expected_version=0,
                    prices=[{"serviceId":service,"amountMinor":1200}])
            return "CREATED"
        except AutoEstimateConflict:
            return "CONFLICT"
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(publish,(0,1)))==["CONFLICT","CREATED"]
    with psycopg.connect(DSN) as db:
        revision=db.execute("""SELECT id FROM auto_work_order_estimate_revision
                               WHERE work_order_id=%s""",(order,)).fetchone()[0]
    barrier=Barrier(2)
    def decide(_):
        barrier.wait(timeout=10)
        try:
            with psycopg.connect(DSN) as db:
                decide_auto_estimate(
                    db,d["tokens"][d["client_a"]],org,loc,order,
                    revision_id=revision,request_key=uuid4(),decision="APPROVED")
            return "CREATED"
        except AutoEstimateConflict:
            return "CONFLICT"
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(decide,(0,1)))==["CONFLICT","CREATED"]
    with psycopg.connect(DSN) as db:
        assert db.execute("""SELECT count(*) FROM auto_work_order_estimate_decision
                             WHERE work_order_id=%s""",(order,)).fetchone()[0]==1
