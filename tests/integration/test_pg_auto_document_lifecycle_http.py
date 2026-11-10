"""PG17+HTTPS: intake -> approved quote -> QC -> zero due -> POSTED -> CLIENT_ACK -> technical CLOSED.

No legal signature, real payment, actual vehicle handover or owner-independent signing.
"""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import UUID,uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient

from roboport_api.main import app
from roboport_api.auto_workshop_qc import submit_work_order_inspection,record_work_order_qc
from roboport_api.auto_intake_act import write_intake_act,review_intake_act
from roboport_api.auto_work_order_estimate import publish_auto_estimate,decide_auto_estimate
from roboport_api.auto_work_order_execution import (
    start_execution,labor_execution,finish_execution,
)
from roboport_api.auto_demo_close import simulate_cash,seal_handover_preview
from roboport_api.auto_document_lifecycle import (
    record_document_event,DocumentLifecycleConflict,
)
from test_pg_auto_intake_act_http import custody
from test_pg_auto_work_order_estimate_http import scenario as estimate_scenario
from test_pg_auto_work_order_execution_http import session,post
from test_pg_auto_stock_service import grant
from test_pg_baseline import DSN


def ready(monkeypatch):
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTO_API","1")
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTH_API","1")
    monkeypatch.setenv("ROBOport_DATABASE_URL",DSN)
    d,org,loc,order,service,_,_=estimate_scenario()
    with psycopg.connect(DSN) as db:
        grant(db,d["manager"],org,loc,"MECHANIC")
        grant(db,d["other"],org,loc,"MECHANIC")
        grant(db,d["intake"],org,loc,"WAREHOUSE")
        submit_work_order_inspection(
            db,d["tokens"][d["intake"]],org,loc,order,
            odometer_km=6000,fuel_percent=50,tyres_ok=True,lights_ok=True,
            fluid_leak=False,exterior_code="CLEAR")
        intake=write_intake_act(
            db,d["tokens"][d["intake"]],org,loc,order,
            request_key=uuid4(),expected_version=0,
            act=custody(None,damaged=False))["items"][-1]
        reviewed=review_intake_act(
            db,d["tokens"][d["client_a"]],org,loc,order,UUID(intake["id"]),
            request_key=uuid4(),fingerprint=intake["sha256"],
            decision="ACKNOWLEDGED",reason_code="NONE")
        assert reviewed["locked"] is True
        rev=publish_auto_estimate(
            db,d["tokens"][d["intake"]],org,loc,order,
            request_key=uuid4(),expected_version=0,
            prices=[{"serviceId":service,"amountMinor":2500}])["items"][-1]["id"]
        decide_auto_estimate(
            db,d["tokens"][d["client_a"]],org,loc,order,
            revision_id=UUID(rev),request_key=uuid4(),decision="APPROVED")
        start_execution(db,d["tokens"][d["intake"]],org,loc,order,uuid4())
        labor_execution(db,d["tokens"][d["manager"]],org,loc,order,
                        service,45,uuid4())
        finish_execution(db,d["tokens"][d["manager"]],org,loc,order,uuid4())
        record_work_order_qc(db,d["tokens"][d["other"]],org,loc,order,
                             request_key=uuid4(),result="PASS",finding_code="NONE")
        simulate_cash(db,d["tokens"][d["intake"]],org,loc,order,
                      request_key=uuid4(),kind="SIM_PAYMENT",amount_minor=2500)
        sealed=seal_handover_preview(db,d["tokens"][d["intake"]],org,loc,order,
                                     request_key=uuid4())
        assert sealed["preview"]["snapshot"]["simulatedDueMinor"]==0
        assert sealed["releaseAllowed"] is False
        preview=sealed["preview"]
    return d,org,loc,order,intake,preview


def path(org,loc,order):
    return (f"/api/v1/dev/auto/organizations/{org}/locations/{loc}/orders/"
            f"{order}/demo-close")


def event(http,base,head,act,preview,action,key=None,**overrides):
    payload={
      "requestKey":str(key or uuid4()),
      "action":action,"previewId":preview["id"],
      "previewDigest":preview["digest"],"intakeActId":act["id"],
      "intakeDigest":act["sha256"],
    }
    payload.update(overrides)
    return http.post(base+"/document-events",headers=head,json=payload)


def test_document_full_transition_privacy_immutability_and_replay(monkeypatch):
    d,org,loc,order,act,preview=ready(monkeypatch)
    http=TestClient(app,base_url="https://127.0.0.1")
    base=path(org,loc,order)
    assert http.get(base).status_code==403
    head=session(http,d,d["intake"])
    before=http.get(base)
    assert before.status_code==200,before.text
    assert before.json()["documentWorkflow"]["status"]=="DRAFT"
    assert before.json()["documentWorkflow"]["intakeAct"]["clientDecision"]=="ACKNOWLEDGED"
    assert event(http,base,head,act,preview,"CLOSED_TECHNICAL").status_code==409
    assert event(http,base,head,act,preview,"CLIENT_ACK").status_code==403
    assert event(http,base,head,act,preview,"POSTED",
                 previewDigest="a"*64).status_code==409
    assert event(http,base,head,act,preview,"POSTED",
                 intakeActId=str(uuid4())).status_code==409
    assert event(http,base,head,act,preview,"POSTED",
                 actionOverride=True).status_code==422
    assert event(http,base,{},act,preview,"POSTED").status_code==403
    key=uuid4()
    posting=event(http,base,head,act,preview,"POSTED",key)
    assert posting.status_code==201,posting.text
    assert posting.headers["cache-control"]=="no-store"
    assert posting.json()["status"]=="POSTED"
    assert posting.json()["legalSignatureStatus"]=="NOT_IMPLEMENTED"
    assert posting.json()["releaseAllowed"] is False
    assert event(http,base,head,act,preview,"POSTED",key).status_code==200
    assert event(http,base,head,act,preview,"POSTED",key,
                 previewDigest="b"*64).status_code==409
    assert event(http,base,head,act,preview,"POSTED").status_code==409
    assert http.get(base).json()["documentWorkflow"]["status"]=="POSTED"
    unauthorized=session(http,d,d["client_b"])
    assert event(http,base,unauthorized,act,preview,"CLIENT_ACK").status_code==403
    assert http.get(base).status_code==403
    owner=session(http,d,d["client_a"])
    assert event(http,base,owner,act,preview,"CLOSED_TECHNICAL").status_code==403
    decision_key=uuid4()
    decision=event(http,base,owner,act,preview,"CLIENT_ACK",decision_key)
    assert decision.status_code==201,decision.text
    assert decision.json()["status"]=="CLIENT_ACKNOWLEDGED"
    assert event(http,base,owner,act,preview,"CLIENT_ACK",decision_key).status_code==200
    assert event(http,base,owner,act,preview,"CLIENT_DISPUTE").status_code==409
    assert event(http,base,owner,act,preview,"CLIENT_ACK",decision_key,
                 intakeDigest="c"*64).status_code==409
    head=session(http,d,d["intake"])
    closing_key=uuid4()
    closing=event(http,base,head,act,preview,"CLOSED_TECHNICAL",closing_key)
    assert closing.status_code==201,closing.text
    assert closing.json()["status"]=="CLOSED_TECHNICAL"
    assert closing.json()["releaseAllowed"] is False
    assert event(http,base,head,act,preview,"CLOSED_TECHNICAL",closing_key).status_code==200
    assert event(http,base,head,act,preview,"CLOSED_TECHNICAL").status_code==409
    owner=session(http,d,d["client_a"])
    visible=http.get(base)
    assert visible.status_code==200
    workflow=visible.json()["documentWorkflow"]
    assert workflow["status"]=="CLOSED_TECHNICAL"
    assert [x["action"] for x in workflow["events"]]==[
      "POSTED","CLIENT_ACK","CLOSED_TECHNICAL"]
    assert {x["previewDigest"] for x in workflow["events"]}=={preview["digest"]}
    assert {x["intakeDigest"] for x in workflow["events"]}=={act["sha256"]}
    assert visible.json()["legalGatePassed"] is False
    with psycopg.connect(DSN) as db:
        assert db.execute("""SELECT count(*) FROM auto_order_demo_document_event
          WHERE work_order_id=%s""",(order,)).fetchone()==(3,)
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("""UPDATE auto_order_demo_document_event
                   SET action='CLIENT_DISPUTE' WHERE work_order_id=%s""",(order,))
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("""DELETE FROM auto_order_demo_document_event
                   WHERE work_order_id=%s""",(order,))
        # Even a forged SQL caller cannot make a CLIENT event under another user.
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("""
                  INSERT INTO auto_order_demo_document_event(
                    id,work_order_id,preview_id,preview_digest,intake_act_id,
                    intake_digest,actor_id,request_key,action)
                  VALUES(gen_random_uuid(),%s,%s,%s,%s,%s,%s,
                         gen_random_uuid(),'CLIENT_DISPUTE')
                """,(order,UUID(preview["id"]),preview["digest"],
                     UUID(act["id"]),act["sha256"],d["client_b"]))

    # Revoke original proof; no read or exact replay after revocation.
    with psycopg.connect(DSN) as db:
        db.execute("""UPDATE auto_vehicle_customer_link SET revoked_at=now()
          WHERE id=%s""",(d["link"],))
    assert http.get(base).status_code==403
    assert event(http,base,owner,act,preview,"CLIENT_ACK",decision_key).status_code==403


def test_client_dispute_is_terminal_for_synthetic_document_close(monkeypatch):
    d,org,loc,order,act,preview=ready(monkeypatch)
    http=TestClient(app,base_url="https://127.0.0.1")
    base=path(org,loc,order)
    manager=session(http,d,d["intake"])
    assert event(http,base,manager,act,preview,"POSTED").status_code==201
    owner=session(http,d,d["client_a"])
    denied=event(http,base,owner,act,preview,"CLIENT_DISPUTE")
    assert denied.status_code==201,denied.text
    assert denied.json()["status"]=="CLIENT_DISPUTED"
    manager=session(http,d,d["intake"])
    assert event(http,base,manager,act,preview,"CLOSED_TECHNICAL").status_code==409
    assert http.get(base).json()["documentWorkflow"]["canClose"] is False


def test_concurrent_document_post_and_client_review_have_exactly_one_winner(monkeypatch):
    d,org,loc,order,act,preview=ready(monkeypatch)
    barrier=Barrier(2)
    def post_attempt(_):
        barrier.wait(timeout=15)
        try:
            with psycopg.connect(DSN) as db:
                result=record_document_event(
                    db,d["tokens"][d["intake"]],org,loc,order,
                    request_key=uuid4(),action="POSTED",
                    preview_id=UUID(preview["id"]),preview_digest=preview["digest"],
                    intake_act_id=UUID(act["id"]),intake_digest=act["sha256"])
                return result["status"]
        except DocumentLifecycleConflict:
            return "CONFLICT"
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(post_attempt,range(2)))==["CONFLICT","POSTED"]
    with psycopg.connect(DSN) as db:
        assert db.execute("""SELECT count(*) FROM auto_order_demo_document_event
          WHERE work_order_id=%s""",(order,)).fetchone()==(1,)
