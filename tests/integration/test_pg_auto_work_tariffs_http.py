"""PG17/HTTPS tests for branch tariff governance and quote policy binding."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4
import json

import psycopg
import pytest

from roboport_api.auto_work_tariffs import propose_tariff,TariffConflict
from test_pg_auto_work_order_estimate_http import scenario,api,token,path as estimate_path
from test_pg_auto_norm_hour_estimate_http import factored
from test_pg_auto_stock_service import grant
from test_pg_baseline import DSN


def setup(monkeypatch):
    d,org,loc,order,service,foreign,_=scenario()
    http=api(monkeypatch)
    with psycopg.connect(DSN) as db:
        grant(db,d["manager"],org,loc,"MANAGER")
    return d,org,loc,order,service,foreign,http


def policy_url(org,loc):
    return f"/api/v1/dev/auto/organizations/{org}/locations/{loc}/tariff-policies"


def proposal(*,category="MASTER",rate=10001,discount=1000,version=0,key=None):
    return {"requestKey":str(key or uuid4()),"expectedVersion":version,
            "qualificationCode":category,"rateMinorPerHour":rate,
            "maxDiscountBps":discount}


def test_branch_tariffs_need_independent_approval_and_enforce_quote(monkeypatch):
    d,org,loc,order,service,foreign,http=setup(monkeypatch)
    policies=policy_url(org,loc)
    quote=estimate_path(org,loc,order)
    assert http.get(policies).status_code==403
    proposer=token(http,d,d["intake"])
    assert http.get(policies).json()["items"]==[]
    body=proposal(key=uuid4())
    assert http.post(policies,json=body).status_code==403
    assert http.post(policies,headers=proposer,json={
        **body,"rateMinorPerHour":-1}).status_code==422
    first=http.post(policies,headers=proposer,json=body)
    assert first.status_code==201,first.text
    row=first.json()["items"][0]
    assert row["version"]==1 and row["approvedBy"] is None
    assert http.post(policies,headers=proposer,json=body).json()["replayed"] is True
    assert http.post(policies,headers=proposer,json={
        **body,"rateMinorPerHour":10002}).status_code==409
    assert http.post(policies,headers=proposer,json=proposal(version=1)
                     ).status_code==409  # cannot skip unresolved approval
    assert http.post(policies+"/"+row["id"]+"/approve",
                     headers=proposer,json={"requestKey":str(uuid4())}).status_code==403
    checker=token(http,d,d["manager"])
    approve_key=uuid4()
    accepted=http.post(policies+"/"+row["id"]+"/approve",
        headers=checker,json={"requestKey":str(approve_key)})
    assert accepted.status_code==201,accepted.text
    assert accepted.json()["active"]["MASTER"]["id"]==row["id"]
    assert http.post(policies+"/"+row["id"]+"/approve",
        headers=checker,json={"requestKey":str(approve_key)}).json()["replayed"] is True

    proposer=token(http,d,d["intake"])
    # Once governed, arbitrary manual prices and unapproved rate/discount are forbidden.
    from test_pg_auto_work_order_estimate_http import publish_payload
    assert http.post(quote,headers=proposer,
        json=publish_payload(service)).status_code==409
    assert http.post(quote,headers=proposer,json=factored(
        service,rate=10002)).status_code==409
    assert http.post(quote,headers=proposer,json=factored(
        service,discount=1001)).status_code==409
    assert http.post(quote,headers=proposer,json=factored(
        service,category="GENERAL")).status_code==409
    payload=factored(service,key=uuid4())
    created=http.post(quote,headers=proposer,json=payload)
    assert created.status_code==201,created.text
    version=created.json()["items"][0]
    assert version["totalMinor"]==16877
    assert version["tariffs"]==[{
        "serviceId":str(service),"policyId":row["id"],"version":1,
        "qualificationCode":"MASTER","rateMinorPerHour":10001,
        "maxDiscountBps":1000}]
    assert http.post(quote,headers=proposer,json=payload).json()["replayed"] is True
    customer=token(http,d,d["client_a"])
    assert http.get(quote).json()["items"][0]["tariffs"]==version["tariffs"]
    assert http.post(quote+"/decision",headers=customer,json={
        "revisionId":version["id"],"requestKey":str(uuid4()),
        "decision":"APPROVED"}).status_code==201
    with psycopg.connect(DSN) as db:
        assert db.execute("""SELECT tariff_snapshot FROM auto_work_order_estimate_revision
          WHERE id=%s""",(version["id"],)).fetchone()[0]==version["tariffs"]
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("""UPDATE auto_work_tariff_revision
                  SET max_discount_bps=2500 WHERE id=%s""",(row["id"],))
    token(http,d,d["client_b"])
    assert http.get(quote).status_code==403
    proposer=token(http,d,d["intake"])
    assert http.get(policies.replace(str(loc),str(uuid4()))).status_code==403


def test_new_approved_tariff_supersedes_old_rate_without_editing_old_quote(monkeypatch):
    d,org,loc,order,service,_,http=setup(monkeypatch)
    policies=policy_url(org,loc)
    quote=estimate_path(org,loc,order)
    proposer=token(http,d,d["intake"])
    first=http.post(policies,headers=proposer,json=proposal()).json()["items"][0]
    checker=token(http,d,d["manager"])
    assert http.post(policies+"/"+first["id"]+"/approve",
        headers=checker,json={"requestKey":str(uuid4())}).status_code==201
    proposer=token(http,d,d["intake"])
    original=http.post(quote,headers=proposer,json=factored(service))
    assert original.status_code==201,original.text
    old=original.json()["items"][0]
    second=http.post(policies,headers=proposer,
        json=proposal(rate=20001,discount=500,version=1))
    assert second.status_code==201,second.text
    next_policy=second.json()["items"][-1]
    checker=token(http,d,d["manager"])
    assert http.post(policies+"/"+next_policy["id"]+"/approve",
        headers=checker,json={"requestKey":str(uuid4())}).status_code==201
    proposer=token(http,d,d["intake"])
    assert http.post(quote,headers=proposer,json=factored(
        service,rate=10001,version=1)).status_code==409
    updated=http.post(quote,headers=proposer,json=factored(
        service,rate=20001,discount=500,version=1))
    assert updated.status_code==201,updated.text
    assert updated.json()["items"][0]["tariffs"]==old["tariffs"]
    assert updated.json()["items"][0]["totalMinor"]==16877
    assert updated.json()["items"][1]["tariffs"][0]["policyId"]==next_policy["id"]
    assert updated.json()["items"][1]["tariffs"][0]["maxDiscountBps"]==500


def test_sql_cannot_forge_tariff_snapshot_or_excess_discount(monkeypatch):
    d,org,loc,order,service,_,http=setup(monkeypatch)
    policies=policy_url(org,loc)
    proposer=token(http,d,d["intake"])
    first=http.post(policies,headers=proposer,json=proposal()).json()["items"][0]
    checker=token(http,d,d["manager"])
    assert http.post(policies+"/"+first["id"]+"/approve",headers=checker,
        json={"requestKey":str(uuid4())}).status_code==201
    correct={"serviceId":str(service),"code":"TEST-ORDER-SVC",
        "title":"SYNTHETIC INSPECTION","durationMinutes":45,
        "pricingMethod":"NORM_HOUR_FACTORED","qualificationCode":"MASTER",
        "rateMinorPerHour":10001,"quantity":2,"coefficientBps":12500,
        "discountBps":1000,"amountMinor":16877}
    snapshot=[{"serviceId":str(service),"policyId":first["id"],
        "version":1,"qualificationCode":"MASTER",
        "rateMinorPerHour":10001,"maxDiscountBps":1000}]
    cases=[
        (correct,[]),
        ({**correct,"rateMinorPerHour":10002,"amountMinor":16878},snapshot),
        ({**correct,"discountBps":1001,"amountMinor":16875},snapshot),
        (correct,[{**snapshot[0],"maxDiscountBps":2500}]),
        (correct,[{**snapshot[0],"policyId":str(uuid4())}]),
    ]
    with psycopg.connect(DSN) as db:
        for line,tariffs in cases:
            with pytest.raises(psycopg.errors.RaiseException):
                with db.transaction():
                    db.execute("""INSERT INTO auto_work_order_estimate_revision
                      (id,work_order_id,organization_id,location_id,version,actor_id,
                       request_key,fingerprint,total_minor,service_lines,
                       diagnostics_snapshot,tariff_snapshot)
                      VALUES(%s,%s,%s,%s,1,%s,%s,%s,%s,%s::jsonb,'[]'::jsonb,%s::jsonb)
                    """,(uuid4(),order,org,loc,d["intake"],uuid4(),"a"*64,
                        line["amountMinor"],json.dumps([line]),json.dumps(tariffs)))
        assert db.execute("""SELECT count(*) FROM auto_work_order_estimate_revision
          WHERE work_order_id=%s""",(order,)).fetchone()==(0,)


def test_concurrent_tariff_proposals_serialize(monkeypatch):
    d,org,loc,_,_,_,http=setup(monkeypatch)
    barrier=Barrier(2)
    def attempt(rate):
        barrier.wait(timeout=15)
        try:
            with psycopg.connect(DSN) as db:
                propose_tariff(db,d["tokens"][d["intake"]],org,loc,
                    request_key=uuid4(),expected_version=0,
                    qualification_code="GENERAL",rate_minor_per_hour=rate,
                    max_discount_bps=500)
            return "CREATED"
        except TariffConflict:
            return "CONFLICT"
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(attempt,[12345,23456]))==["CONFLICT","CREATED"]
