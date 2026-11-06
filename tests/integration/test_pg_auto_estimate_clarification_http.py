"""Full synthetic CLIENT -> MANAGER -> CLIENT clarification loop on immutable estimates."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import psycopg
import pytest

from roboport_api.auto_work_order_estimate import (
    AutoEstimateConflict, request_estimate_clarification,
)
from test_pg_baseline import DSN
from test_pg_auto_work_order_estimate_http import (
    scenario, api, token, path, publish_payload,
)


def ask(client,url,revision,reason="PRICE",key=None,headers=None):
    return client.post(url+"/clarification",headers=headers,json={
        "revisionId":str(revision),"reasonCode":reason,
        "requestKey":str(key or uuid4())})


def answer(client,url,revision,resolution,key=None,headers=None):
    return client.post(url+"/clarification/resolve",headers=headers,json={
        "revisionId":str(revision),"resolutionCode":resolution,
        "requestKey":str(key or uuid4())})


def test_two_role_ask_answer_requote_and_client_decision(monkeypatch):
    d,org,loc,order,service,_,_=scenario()
    client=api(monkeypatch)
    url=path(org,loc,order)
    manager=token(client,d,d["intake"])
    first=client.post(url,headers=manager,json=publish_payload(service))
    assert first.status_code==201,first.text
    rev=first.json()["items"][-1]["id"]
    assert ask(client,url,rev).status_code==403
    # Only the owner of the original order may request clarification.
    wrong=token(client,d,d["client_b"])
    assert ask(client,url,rev,headers=wrong).status_code==403
    customer=token(client,d,d["client_a"])
    key=uuid4()
    questioned=ask(client,url,rev,headers=customer,key=key)
    assert questioned.status_code==201,questioned.text
    assert questioned.json()["items"][-1]["clarificationReason"]=="PRICE"
    assert questioned.json()["items"][-1]["clarificationResolution"] is None
    assert questioned.headers["cache-control"]=="no-store"
    assert ask(client,url,rev,headers=customer,key=key).status_code==200
    assert ask(client,url,rev,headers=customer,key=key,reason="TIMING").status_code==409
    assert ask(client,url,rev,headers=customer).status_code==409
    assert ask(client,url,rev,headers=customer,reason="free text").status_code==422
    blocked=client.post(url+"/decision",headers=customer,json={
        "revisionId":rev,"requestKey":str(uuid4()),"decision":"APPROVED"})
    assert blocked.status_code==409
    assert blocked.json()["code"]=="CLARIFICATION_PENDING"
    manager=token(client,d,d["intake"])
    assert client.post(url,headers=manager,json=publish_payload(
        service,version=1,amount=10500)).status_code==409
    assert answer(client,url,rev,"EXPLAINED",headers=customer).status_code==403
    reply_key=uuid4()
    resolved=answer(client,url,rev,"NEW_VERSION_REQUIRED",headers=manager,key=reply_key)
    assert resolved.status_code==201,resolved.text
    assert resolved.json()["items"][-1]["clarificationResolution"]=="NEW_VERSION_REQUIRED"
    assert answer(client,url,rev,"NEW_VERSION_REQUIRED",headers=manager,
                  key=reply_key).status_code==200
    assert answer(client,url,rev,"EXPLAINED",headers=manager,key=reply_key).status_code==409
    # Still cannot approve the old version: manager must actually issue a new one.
    customer=token(client,d,d["client_a"])
    assert client.post(url+"/decision",headers=customer,json={
        "revisionId":rev,"requestKey":str(uuid4()),"decision":"APPROVED"
    }).status_code==409
    manager=token(client,d,d["intake"])
    updated=client.post(url,headers=manager,json=publish_payload(
        service,version=1,amount=10500))
    assert updated.status_code==201,updated.text
    new_revision=updated.json()["items"][-1]["id"]
    assert updated.json()["latestVersion"]==2
    customer=token(client,d,d["client_a"])
    assert ask(client,url,rev,headers=customer).status_code==409
    decision=client.post(url+"/decision",headers=customer,json={
        "revisionId":new_revision,"requestKey":str(uuid4()),"decision":"APPROVED"})
    assert decision.status_code==201,decision.text
    assert decision.json()["items"][-1]["decision"]=="APPROVED"
    assert decision.json()["items"][0]["decision"] is None
    with psycopg.connect(DSN) as db:
        assert db.execute("SELECT count(*) FROM auto_estimate_clarification_request").fetchone()[0]>=1
        assert db.execute("""SELECT count(*) FROM auto_estimate_clarification_reply answer
          JOIN auto_estimate_clarification_request q ON q.id=answer.request_id
          WHERE q.work_order_id=%s""",(order,)).fetchone()[0]==1
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("DELETE FROM auto_estimate_clarification_request WHERE revision_id=%s",
                           (rev,))


def test_explained_allows_same_revision_decision_but_revocation_blocks_replay(monkeypatch):
    d,org,loc,order,service,_,_=scenario()
    client=api(monkeypatch)
    url=path(org,loc,order)
    manager=token(client,d,d["intake"])
    rev=client.post(url,headers=manager,json=publish_payload(service)).json()["items"][-1]["id"]
    customer=token(client,d,d["client_a"])
    key=uuid4()
    assert ask(client,url,rev,key=key,headers=customer).status_code==201
    manager=token(client,d,d["intake"])
    assert answer(client,url,rev,"EXPLAINED",headers=manager).status_code==201
    customer=token(client,d,d["client_a"])
    assert client.post(url+"/decision",headers=customer,json={
        "revisionId":rev,"requestKey":str(uuid4()),"decision":"DECLINED"
    }).status_code==201
    assert ask(client,url,rev,key=key,headers=customer).status_code==409
    with psycopg.connect(DSN) as db:
        db.execute("UPDATE auto_vehicle_customer_link SET revoked_at=now() WHERE id=%s",
                   (d["link"],))
    assert client.get(url).status_code==403
    assert ask(client,url,rev,key=key,headers=customer).status_code==403
    manager=token(client,d,d["intake"])
    assert answer(client,url,rev,"EXPLAINED",headers=manager).status_code==403


def test_racing_customer_questions_only_one_immutable_request(monkeypatch):
    d,org,loc,order,service,_,_=scenario()
    client=api(monkeypatch)
    url=path(org,loc,order)
    manager=token(client,d,d["intake"])
    rev=client.post(url,headers=manager,json=publish_payload(service)).json()["items"][-1]["id"]
    barrier=Barrier(2)
    def attempt(_):
        barrier.wait(timeout=10)
        try:
            with psycopg.connect(DSN) as db:
                request_estimate_clarification(
                    db,d["tokens"][d["client_a"]],org,loc,order,
                    revision_id=__import__("uuid").UUID(rev),
                    request_key=uuid4(),reason_code="PRICE")
            return "CREATED"
        except AutoEstimateConflict:
            return "CONFLICT"
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(attempt,(1,2)))==["CONFLICT","CREATED"]
