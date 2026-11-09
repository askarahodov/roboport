"""Сквозная PG17+HTTPS сверка основной сметы и согласованных допработ (#441)."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import UUID,uuid4
import json

import psycopg
import pytest
from fastapi.testclient import TestClient

from roboport_api.main import app
from roboport_api.auto_demo_close import DemoCloseConflict,simulate_cash
from test_pg_baseline import DSN
from test_pg_auto_work_order_additional_http import setup,quote,extra_path
from test_pg_auto_work_order_execution_http import session,post,url


def _settlement(monkeypatch,decision="APPROVED"):
    d,org,loc,order,base_service,additional,base=setup(monkeypatch)
    http=TestClient(app,base_url="https://127.0.0.1")
    manager=session(http,d,d["intake"])
    published=http.post(extra_path(org,loc,order),headers=manager,
                        json=quote(additional,base))
    assert published.status_code==201,published.text
    extra=published.json()["items"][-1]
    customer=session(http,d,d["client_a"])
    decided=http.post(extra_path(org,loc,order)+"/decision",headers=customer,
                      json={"revisionId":extra["id"],"requestKey":str(uuid4()),
                            "decision":decision})
    assert decided.status_code==201,decided.text
    operator=session(http,d,d["intake"])
    execution=url(org,loc,order)
    assert post(http,execution+"/start",operator).status_code==201
    mechanic=session(http,d,d["manager"])
    assert post(http,execution+"/labor",mechanic,serviceId=str(base_service),
                actualMinutes=45).status_code==201
    if decision=="APPROVED":
        assert post(http,execution+"/labor",mechanic,serviceId=str(additional),
                    actualMinutes=30).status_code==201
    assert post(http,execution+"/finish",mechanic).status_code==201
    checker=session(http,d,d["other"])
    assert post(http,execution.removesuffix("/execution")+"/qc",checker,
                result="PASS",findingCode="NONE").status_code==201
    return d,org,loc,order,base,extra,http


def _base(org,loc,order):
    return f"/api/v1/dev/auto/organizations/{org}/locations/{loc}/orders/{order}/demo-close"


def test_approved_extra_is_billable_and_immutable_preview_matches_real_consent(monkeypatch):
    d,org,loc,order,base_id,extra,http=_settlement(monkeypatch)
    base=_base(org,loc,order)
    manager=session(http,d,d["intake"])
    state=http.get(base)
    assert state.status_code==200,state.text
    assert state.json()["approvedBaseMinor"]==2500
    assert state.json()["approvedExtraMinor"]==11251
    assert state.json()["approvedExtraRevisionId"]==extra["id"]
    assert state.json()["approvedExtraDigest"]==extra["digest"]
    assert state.json()["approvedTotalMinor"]==13751
    assert state.json()["simulatedDueMinor"]==13751
    cash=base+"/simulated-cash"

    first=post(http,cash,manager,kind="SIM_PAYMENT",amountMinor=2500)
    assert first.status_code==201,first.text
    assert first.json()["simulatedDueMinor"]==11251
    assert post(http,base+"/seal-preview",manager).status_code==409

    # Триггер SQL независимо отклоняет превышение согласованного итога.
    with psycopg.connect(DSN) as db:
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("""
                  INSERT INTO auto_order_demo_cash_event
                    (id,work_order_id,revision_id,actor_id,request_key,kind,amount_minor)
                  VALUES(gen_random_uuid(),%s,%s,%s,gen_random_uuid(),'SIM_PAYMENT',11252)
                """,(order,base_id,d["intake"]))
    assert post(http,cash,manager,kind="SIM_PAYMENT",amountMinor=11252).status_code==409
    second=post(http,cash,manager,kind="SIM_PAYMENT",amountMinor=11251)
    assert second.status_code==201,second.text
    assert second.json()["simulatedDueMinor"]==0
    # Попытка подделать перечень услуг при совпадающих итогах и UUID
    # отклоняется PostgreSQL до фиксации immutable-пакета.
    with psycopg.connect(DSN) as db:
        estimate=db.execute("""
          SELECT version,fingerprint,service_lines
          FROM auto_work_order_estimate_revision WHERE id=%s
        """,(base_id,)).fetchone()
        fake={
          "kind":"SYNTHETIC_PRE_HANDOVER_PREVIEW",
          "workOrderId":str(order),"estimateRevisionId":str(base_id),
          "estimateVersion":estimate[0],"estimateDigest":estimate[1].strip(),
          "currency":"RUB","lines":[],  # Подмена базовых услуг.
          "approvedBaseMinor":2500,"approvedExtraMinor":11251,
          "extraRevisionId":extra["id"],"extraVersion":extra["version"],
          "extraDigest":extra["digest"],"extraLines":extra["lines"],
          "approvedTotalMinor":13751,"simulatedPaidMinor":13751,
          "simulatedRefundedMinor":0,"simulatedDueMinor":0,
          "independentQc":"PASS","stockReservations":0,"unreceivedMaterials":0,
          "legalGatePassed":False,"releaseAllowed":False,
        }
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("""
                  INSERT INTO auto_order_demo_handover_preview
                  (id,work_order_id,revision_id,actor_id,request_key,fingerprint,snapshot)
                  VALUES(gen_random_uuid(),%s,%s,%s,gen_random_uuid(),%s,%s::jsonb)
                """,(order,base_id,d["intake"],"0"*64,json.dumps(fake)))
    sealed=post(http,base+"/seal-preview",manager)
    assert sealed.status_code==201,sealed.text
    snapshot=sealed.json()["preview"]["snapshot"]
    assert snapshot["approvedBaseMinor"]==2500
    assert snapshot["approvedExtraMinor"]==11251
    assert snapshot["approvedTotalMinor"]==13751
    assert snapshot["extraRevisionId"]==extra["id"]
    assert snapshot["extraDigest"]==extra["digest"]
    assert snapshot["extraLines"]==extra["lines"]
    assert snapshot["simulatedPaidMinor"]==13751
    assert snapshot["releaseAllowed"] is False
    assert snapshot["legalGatePassed"] is False

    customer=session(http,d,d["client_a"])
    view=http.get(base)
    assert view.status_code==200
    assert view.json()["preview"]["snapshot"]["extraDigest"]==extra["digest"]
    assert post(http,cash,customer,kind="SIM_PAYMENT",amountMinor=1).status_code==403


def test_declined_extra_does_not_enter_balance_and_cross_branch_is_denied(monkeypatch):
    d,org,loc,order,base_id,extra,http=_settlement(monkeypatch,decision="DECLINED")
    base=_base(org,loc,order)
    manager=session(http,d,d["intake"])
    state=http.get(base)
    assert state.status_code==200
    assert state.json()["approvedBaseMinor"]==2500
    assert state.json()["approvedExtraMinor"]==0
    assert state.json()["approvedExtraRevisionId"] is None
    assert state.json()["approvedTotalMinor"]==2500
    assert http.get(base.replace(str(loc),str(uuid4()))).status_code==403
    assert post(http,base+"/simulated-cash",manager,kind="SIM_PAYMENT",
                amountMinor=2501).status_code==409
    assert post(http,base+"/simulated-cash",manager,kind="SIM_PAYMENT",
                amountMinor=2500).status_code==201
    sealed=post(http,base+"/seal-preview",manager)
    assert sealed.status_code==201,sealed.text
    assert sealed.json()["preview"]["snapshot"]["extraRevisionId"] is None
    assert sealed.json()["preview"]["snapshot"]["approvedTotalMinor"]==2500


def test_approved_extra_parallel_cash_cannot_exceed_combined_total(monkeypatch):
    d,org,loc,order,base_id,extra,http=_settlement(monkeypatch)
    barrier=Barrier(2)
    def attempt(_):
        barrier.wait(timeout=15)
        try:
            with psycopg.connect(DSN) as db:
                simulate_cash(db,d["tokens"][d["intake"]],org,loc,order,
                              request_key=uuid4(),kind="SIM_PAYMENT",amount_minor=9000)
            return "RECORDED"
        except DemoCloseConflict:
            return "CONFLICT"
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(attempt,[0,1]))==["CONFLICT","RECORDED"]
    with psycopg.connect(DSN) as db:
        amount=db.execute("""SELECT sum(amount_minor) FROM auto_order_demo_cash_event
                              WHERE work_order_id=%s AND kind='SIM_PAYMENT'""",
                          (order,)).fetchone()[0]
        assert amount==9000
