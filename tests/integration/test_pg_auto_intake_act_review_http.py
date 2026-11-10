"""PG17 + HTTPS: immutable intake review, disputes, revocation and races.

No legal signature. Uses real session, customer/proof ACL and PostgreSQL
triggers with synthetic data; UI tests are additional visual evidence only.
"""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import UUID,uuid4
import psycopg
import pytest

from roboport_api.auto_intake_act import (
    IntakeActConflict,review_intake_act,
)
from test_pg_auto_intake_act_http import (
    custody,publish,synthetic_png,url,
)
from test_pg_auto_workshop_qc_http import (
    scenario,client_setup,token_as,inspection,target,
)
from test_pg_baseline import DSN


def prepared(monkeypatch):
    d,org,loc,_,order,_,_,_,foreign_loc,_,_=scenario()
    http=client_setup(monkeypatch)
    staff=token_as(http,d,d["intake"])
    _,inspect,_=target(org,loc,order)
    assert http.post(inspect,headers=staff,json=inspection()).status_code==201
    act=custody(synthetic_png())
    path=url(org,loc,order)
    first=publish(http,staff,path,act)
    assert first.status_code==201,first.text
    version=first.json()["items"][-1]
    assert version["review"] is None
    return d,org,loc,order,foreign_loc,http,path,act,version


def review(http,path,act,headers,decision="ACKNOWLEDGED",reason="NONE",key=None,
           fingerprint=None):
    return http.post(path+"/"+act["id"]+"/review",headers=headers,json={
      "requestKey":str(key or uuid4()),
      "fingerprint":fingerprint or act["sha256"],
      "decision":decision,"reasonCode":reason,
    })


def test_intake_acknowledgement_is_locked_to_original_client_act_and_digest(monkeypatch):
    d,org,loc,order,foreign_loc,http,path,act,first=prepared(monkeypatch)
    staff=token_as(http,d,d["intake"])
    assert review(http,path,first,staff).status_code==403
    guest=token_as(http,d,d["client_b"])
    assert review(http,path,first,guest).status_code==403
    assert http.get(path+"/"+first["id"]+"/photos/1").status_code==403
    customer=token_as(http,d,d["client_a"])
    assert review(http,path,first,customer,fingerprint="a"*64).status_code==409
    assert review(http,path,first,customer,decision="DISPUTED",reason="NONE").status_code==422
    assert review(http,path,first,customer,decision="ACKNOWLEDGED",
                  reason="OTHER").status_code==422
    assert http.post(path+"/"+first["id"]+"/review",headers=customer,
      json={"requestKey":str(uuid4()),"fingerprint":first["sha256"],
            "decision":"ACKNOWLEDGED","reasonCode":"NONE","legalSignature":True}
      ).status_code==422
    assert review(http,path,first,customer,decision="DISPUTED",
                  reason="INVENTORY_MISMATCH",key=uuid4(),
                  fingerprint=first["sha256"].upper()).status_code==422

    key=uuid4()
    accepted=review(http,path,first,customer,key=key)
    assert accepted.status_code==201,accepted.text
    body=accepted.json()
    assert body["releaseAllowed"] is False
    assert body["legalSignatureStatus"]=="NOT_IMPLEMENTED"
    assert body["locked"] is True
    assert body["items"][0]["review"]["decision"]=="ACKNOWLEDGED"
    assert body["items"][0]["review"]["fingerprint"]==first["sha256"]
    assert "legalSignature" not in body["items"][0]["review"]
    assert review(http,path,first,customer,key=key).status_code==200
    assert review(http,path,first,customer,key=key,decision="DISPUTED",
                  reason="DAMAGE_MISMATCH").status_code==409
    assert review(http,path,first,customer).status_code==409
    assert http.get(path.replace(str(loc),str(foreign_loc))).status_code==403

    staff=token_as(http,d,d["intake"])
    staff_read=http.get(path)
    assert staff_read.status_code==200
    assert staff_read.json()["locked"] is True
    assert staff_read.json()["items"][0]["review"]["decision"]=="ACKNOWLEDGED"
    assert publish(http,staff,path,custody(synthetic_png(),condition="MISSING"),
                   version=1).status_code==409
    with psycopg.connect(DSN) as db:
        record=db.execute("""
          SELECT id,actor_id,act_id,fingerprint,decision,reason_code
          FROM auto_work_order_intake_act_review WHERE act_id=%s
        """,(UUID(first["id"]),)).fetchone()
        assert record[1]==d["client_a"]
        assert record[2]==UUID(first["id"])
        assert record[3:]==(first["sha256"],"ACKNOWLEDGED","NONE")
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("UPDATE auto_work_order_intake_act_review SET decision='DISPUTED' WHERE id=%s",
                           (record[0],))
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("DELETE FROM auto_work_order_intake_act_review WHERE id=%s",
                           (record[0],))
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("""
                  INSERT INTO auto_work_order_intake_act
                    (id,work_order_id,inspection_id,version,actor_id,request_key,
                     fingerprint,vehicle_kind,damages,inventory,photo_meta,photo_bytes)
                  SELECT gen_random_uuid(),work_order_id,inspection_id,version+1,
                    actor_id,gen_random_uuid(),fingerprint,vehicle_kind,damages,
                    inventory,photo_meta,photo_bytes
                  FROM auto_work_order_intake_act WHERE id=%s
                """,(UUID(first["id"]),))
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("""
                  INSERT INTO auto_work_order_intake_act_review
                    (id,work_order_id,act_id,actor_id,request_key,fingerprint,
                     decision,reason_code)
                  VALUES(gen_random_uuid(),%s,%s,%s,gen_random_uuid(),
                         %s,'ACKNOWLEDGED','NONE')
                """,(order,UUID(first["id"]),d["client_b"],first["sha256"]))

    customer=token_as(http,d,d["client_a"])
    with psycopg.connect(DSN) as db:
        db.execute("""
          UPDATE auto_vehicle_customer_link SET revoked_at=now() WHERE id=%s
        """,(d["link"],))
    assert review(http,path,first,customer,key=key).status_code==403
    assert http.get(path).status_code==403


def test_dispute_allows_new_immutable_version_but_old_one_cannot_be_reaccepted(monkeypatch):
    d,org,loc,order,_,http,path,act,first=prepared(monkeypatch)
    customer=token_as(http,d,d["client_a"])
    disputed=review(http,path,first,customer,decision="DISPUTED",
                    reason="DAMAGE_MISMATCH")
    assert disputed.status_code==201,disputed.text
    assert disputed.json()["locked"] is False
    assert disputed.json()["items"][0]["review"]["reasonCode"]=="DAMAGE_MISMATCH"
    staff=token_as(http,d,d["intake"])
    latest=publish(http,staff,path,custody(synthetic_png(),condition="MISSING"),
                   version=1)
    assert latest.status_code==201,latest.text
    second=latest.json()["items"][1]
    assert second["review"] is None
    customer=token_as(http,d,d["client_a"])
    assert review(http,path,first,customer,key=uuid4()).status_code==409
    accepted=review(http,path,second,customer)
    assert accepted.status_code==201,accepted.text
    latest_state=accepted.json()
    assert latest_state["locked"] is True
    assert [v["review"]["decision"] for v in latest_state["items"]]==[
        "DISPUTED","ACKNOWLEDGED"]
    with psycopg.connect(DSN) as db:
        assert db.execute("""
          SELECT count(*) FROM auto_work_order_intake_act_review WHERE work_order_id=%s
        """,(order,)).fetchone()==(2,)


def test_concurrent_client_review_one_winner_and_no_duplicate_ledger(monkeypatch):
    d,org,loc,order,_,http,path,_,first=prepared(monkeypatch)
    barrier=Barrier(2)
    def submit(decision):
        barrier.wait(timeout=15)
        try:
            with psycopg.connect(DSN) as db:
                return review_intake_act(
                    db,d["tokens"][d["client_a"]],org,loc,order,
                    UUID(first["id"]),request_key=uuid4(),
                    fingerprint=first["sha256"],decision=decision,
                    reason_code="NONE" if decision=="ACKNOWLEDGED" else "OTHER"
                )["replayed"]
        except IntakeActConflict:
            return "CONFLICT"
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(submit,("ACKNOWLEDGED","DISPUTED")))
    assert results.count(False)==1 and results.count("CONFLICT")==1
    with psycopg.connect(DSN) as db:
        assert db.execute("""
           SELECT count(*) FROM auto_work_order_intake_act_review WHERE work_order_id=%s
        """,(order,)).fetchone()==(1,)
