"""Real PG17/HTTPS synthetic cashier: advance → QC → payment/refunds → day close."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import UUID,uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient

from roboport_api.main import app
from roboport_api.auto_treasury import (
    create_account,record_event,close_day,TreasuryConflict,
)
from roboport_api.auto_work_order_execution import (
    start_execution,labor_execution,finish_execution,
)
from roboport_api.auto_workshop_qc import record_work_order_qc
from test_pg_auto_work_order_execution_http import fixture,session,post
from test_pg_auto_stock_service import grant
from test_pg_baseline import DSN


def prepare(monkeypatch):
    d,org,loc,order,service,_,_=fixture(monkeypatch,with_material=False)
    with psycopg.connect(DSN) as db:
        grant(db,d["manager"],org,loc,"CASHIER")
    http=TestClient(app,base_url="https://127.0.0.1")
    base=f"/api/v1/dev/auto/organizations/{org}/locations/{loc}/treasury"
    manager=session(http,d,d["intake"])
    return d,org,loc,order,service,http,base,manager


def create(http,base,headers,code="CASH-01",key=None):
    return http.post(base+"/accounts",headers=headers,json={
        "requestKey":str(key or uuid4()),"code":code,
        "label":"Тестовая касса 01","kind":"CASH_SIM",
    })


def event(http,base,headers,order,account,kind,amount,key=None,**extra):
    return http.post(base+f"/orders/{order}/events",headers=headers,json={
      "requestKey":str(key or uuid4()),"accountId":str(account),
      "kind":kind,"amountMinor":amount,"reason":"Тестовый акт оплаты",
      **extra,
    })


def complete_qc(d,org,loc,order,service):
    with psycopg.connect(DSN) as db:
        start_execution(db,d["tokens"][d["intake"]],org,loc,order,uuid4())
        labor_execution(db,d["tokens"][d["manager"]],org,loc,order,
                        service,45,uuid4())
        finish_execution(db,d["tokens"][d["manager"]],org,loc,order,uuid4())
        record_work_order_qc(db,d["tokens"][d["other"]],org,loc,order,
                             request_key=uuid4(),result="PASS",finding_code="NONE")


def local_day(db,loc):
    return db.execute("""
      SELECT (clock_timestamp() AT TIME ZONE timezone_name)::date
      FROM auto_service_location WHERE id=%s
    """,(loc,)).fetchone()[0].isoformat()


def test_full_cashier_lifecycle_precise_amounts_report_replay_and_immutable(monkeypatch):
    d,org,loc,order,service,http,base,manager=prepare(monkeypatch)
    assert http.get(base+"/accounts").status_code==403
    client=session(http,d,d["client_a"])
    assert http.get(base+"/accounts").status_code==403
    assert create(http,base,client).status_code==403
    manager=session(http,d,d["intake"])
    key=uuid4()
    opened=create(http,base,manager,key=key)
    assert opened.status_code==201,opened.text
    account=UUID(opened.json()["accountId"])
    assert create(http,base,manager,key=key).status_code==200
    assert create(http,base,manager,"CASH-02",key=key).status_code==409
    assert create(http,base,manager,"CASH-01").status_code==409
    assert create(http,base,manager,"bad code").status_code==422
    assert http.get(base+"/accounts").json()["items"][0]["balanceMinor"]==0
    cashier=session(http,d,d["manager"])
    assert http.get(base+"/accounts").status_code==200
    assert create(http,base,cashier,"CASH-09").status_code==403
    assert event(http,base,cashier,order,account,"PAYMENT",100).status_code==409
    assert event(http,base,cashier,order,account,"APPLY_ADVANCE",1).status_code==409
    assert event(http,base,cashier,order,account,"REFUND_ADVANCE",1).status_code==409
    assert event(http,base,cashier,order,account,"ADVANCE",2501).status_code==409
    assert event(http,base,cashier,order,account,"ADVANCE",True).status_code==422
    assert event(http,base,cashier,order,account,"ADVANCE",0).status_code==422
    assert event(http,base,cashier,order,uuid4(),"ADVANCE",1).status_code==403
    assert event(http,base,{},order,account,"ADVANCE",100).status_code==403
    advance_key=uuid4()
    a=event(http,base,cashier,order,account,"ADVANCE",1200,advance_key)
    assert a.status_code==201,a.text
    assert a.json()["unappliedAdvanceMinor"]==1200
    assert a.json()["settledMinor"]==0
    assert a.json()["dueMinor"]==2500
    replay=event(http,base,cashier,order,account,"ADVANCE",1200,advance_key)
    assert replay.status_code==200 and replay.json()["replayed"] is True
    assert event(http,base,cashier,order,account,"ADVANCE",100,advance_key).status_code==409
    refund=event(http,base,cashier,order,account,"REFUND_ADVANCE",200)
    assert refund.status_code==201,refund.text
    assert refund.json()["unappliedAdvanceMinor"]==1000
    assert event(http,base,cashier,order,account,"REFUND_ADVANCE",1001).status_code==409
    assert event(http,base,cashier,order,account,"APPLY_ADVANCE",100).status_code==409
    complete_qc(d,org,loc,order,service)
    assert event(http,base,cashier,order,account,"REFUND_PAYMENT",1).status_code==409
    assert event(http,base,cashier,order,account,"APPLY_ADVANCE",500).status_code==201
    assert event(http,base,cashier,order,account,"PAYMENT",1000).status_code==201
    assert event(http,base,cashier,order,account,"REFUND_PAYMENT",300).status_code==201
    assert event(http,base,cashier,order,account,"REFUND_PAYMENT",1001).status_code==409
    assert event(http,base,cashier,order,account,"PAYMENT",300).status_code==201
    assert event(http,base,cashier,order,account,"PAYMENT",501).status_code==409
    assert event(http,base,cashier,order,account,"PAYMENT",500).status_code==201
    final=event(http,base,cashier,order,account,"APPLY_ADVANCE",500)
    assert final.status_code==201,final.text
    state=final.json()
    assert state["advancedMinor"]==1200
    assert state["paymentMinor"]==1800
    assert state["appliedMinor"]==1000
    assert state["refundedAdvanceMinor"]==200
    assert state["refundedPaymentMinor"]==300
    assert state["unappliedAdvanceMinor"]==0
    assert state["settledMinor"]==2500 and state["dueMinor"]==0
    assert state["cashNetMinor"]==2500
    assert state["realMoney"] is False and state["releaseAllowed"] is False
    assert len(state["events"])==8
    assert http.get(base+"/orders/"+str(order)).json()["dueMinor"]==0
    assert http.get(base+"/orders/"+str(uuid4())).status_code==403
    assert http.get(base+"/orders/not-uuid").status_code==422
    assert event(http,base,cashier,order,account,"PAYMENT",1).status_code==409

    with psycopg.connect(DSN) as db:
        day=local_day(db,loc)
        assert db.execute("""SELECT COUNT(*) FROM auto_treasury_event
          WHERE work_order_id=%s""",(order,)).fetchone()==(8,)
        assert db.execute("""SELECT COUNT(*) FROM auto_order_demo_cash_event
          WHERE work_order_id=%s""",(order,)).fetchone()==(0,)
        assert db.execute("""SELECT COUNT(*) FROM auto_work_order_financial_event
          WHERE work_order_id=%s""",(order,)).fetchone()==(0,)
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("""UPDATE auto_treasury_event SET amount_minor=1
                  WHERE work_order_id=%s""",(order,))
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("""DELETE FROM auto_treasury_account WHERE id=%s""",
                           (account,))
        with pytest.raises(psycopg.errors.RaiseException,match="treasury event date forged"):
            with db.transaction():
                db.execute("""
                  INSERT INTO auto_treasury_event
                  (id,account_id,work_order_id,revision_id,actor_id,request_key,
                   kind,amount_minor,reason,business_day,created_at)
                  SELECT gen_random_uuid(),%s,%s,r.id,%s,gen_random_uuid(),
                         'ADVANCE',1,'Forged timestamp',%s::date,
                         clock_timestamp()+interval '2 days'
                  FROM auto_work_order_estimate_revision r
                  WHERE r.work_order_id=%s LIMIT 1
                """,(account,order,d["manager"],day,order))
    report_path=base+"/report"
    manager=session(http,d,d["intake"])
    rep=http.get(report_path,params={"fromDay":day,"toDay":day})
    assert rep.status_code==200,rep.text
    assert rep.headers["cache-control"]=="no-store"
    assert rep.json()["basis"]=="INDEPENDENT_TREASURY_EVENT_DAYS"
    assert rep.json()["realMoney"] is False
    row=rep.json()["items"][0]
    assert (row["openingMinor"],row["incomingMinor"],
            row["outgoingMinor"],row["closingMinor"])==(0,3000,500,2500)
    assert row["closeId"] is None
    assert str(d["client_a"]) not in rep.text
    cashier=session(http,d,d["manager"])
    assert http.post(base+f"/accounts/{account}/close-day",json={
      "requestKey":str(uuid4()),"businessDay":day}).status_code==403
    close_key=uuid4()
    payload={"requestKey":str(close_key),"businessDay":day}
    closed=http.post(base+f"/accounts/{account}/close-day",
                     headers=cashier,json=payload)
    assert closed.status_code==201,closed.text
    assert closed.json()["openingMinor"]==0
    assert closed.json()["incomingMinor"]==3000
    assert closed.json()["outgoingMinor"]==500
    assert closed.json()["closingMinor"]==2500
    assert http.post(base+f"/accounts/{account}/close-day",
                     headers=cashier,json=payload).status_code==200
    assert http.post(base+f"/accounts/{account}/close-day",headers=cashier,json={
      "requestKey":str(uuid4()),"businessDay":day}).status_code==409
    assert event(http,base,cashier,order,account,"REFUND_PAYMENT",1).status_code==409
    after=http.get(report_path,params={"fromDay":day,"toDay":day})
    assert after.json()["items"][0]["closeId"]==closed.json()["closeId"]
    assert http.get(base+"/accounts").json()["items"][0]["lastClosedDay"]==day
    with psycopg.connect(DSN) as db:
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("DELETE FROM auto_treasury_day_close WHERE account_id=%s",(account,))
    # A second empty account permits a direct-SQL forged close test without
    # interfering with the already sealed day above.
    manager=session(http,d,d["intake"])
    second=create(http,base,manager,"CASH-02")
    assert second.status_code==201,second.text
    account2=UUID(second.json()["accountId"])
    with psycopg.connect(DSN) as db:
        with pytest.raises(psycopg.errors.RaiseException,match="treasury close totals mismatch"):
            with db.transaction():
                db.execute("""
                  INSERT INTO auto_treasury_day_close(
                   id,account_id,business_day,actor_id,request_key,
                   opening_minor,incoming_minor,outgoing_minor,closing_minor)
                  VALUES(gen_random_uuid(),%s,%s::date,%s,
                  gen_random_uuid(),99,0,0,99)
                """,(account2,day,d["manager"]))
    assert http.get(report_path,params={
       "fromDay":"2030-01-02","toDay":"2030-01-01"}).status_code==422
    assert http.get(report_path,params={
       "fromDay":"bad","toDay":day}).status_code==422
    assert http.get(report_path,params={
       "fromDay":"2030-01-01","toDay":"2030-03-01"}).status_code==422
    assert http.get(base+"/accounts").headers["cache-control"]=="no-store"
    with psycopg.connect(DSN) as db:
        db.execute("""UPDATE auto_organization_role_grant SET revoked_at=now()
          WHERE organization_id=%s AND user_id=%s AND role_code='CASHIER' AND
            location_id=%s""",(org,d["manager"],loc))
    assert http.get(base+"/accounts").status_code==403
    assert event(http,base,cashier,order,account,"REFUND_PAYMENT",1).status_code==403


def test_cross_org_cashier_boundaries_and_concurrent_double_payment(monkeypatch):
    d,org,loc,order,service,http,base,manager=prepare(monkeypatch)
    created=create(http,base,manager)
    account=UUID(created.json()["accountId"])
    complete_qc(d,org,loc,order,service)
    cashier=session(http,d,d["manager"])
    assert event(http,base,cashier,order,uuid4(),"PAYMENT",100).status_code==403
    assert http.get(base.replace(str(org),str(uuid4()))+"/accounts").status_code==403
    barrier=Barrier(2)
    def attempt(_):
        barrier.wait(timeout=15)
        try:
            with psycopg.connect(DSN) as db:
                record_event(db,d["tokens"][d["manager"]],org,loc,order,
                  account_id=account,request_key=uuid4(),kind="PAYMENT",
                  amount_minor=2000,reason="Concurrent test payment")
            return "RECORDED"
        except TreasuryConflict:
            return "CONFLICT"
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(attempt,range(2)))==["CONFLICT","RECORDED"]
    manager=session(http,d,d["intake"])
    assert http.get(base+"/orders/"+str(order)).json()["settledMinor"]==2000
    cashier=session(http,d,d["manager"])
    assert event(http,base,cashier,order,account,"PAYMENT",501).status_code==409
    assert event(http,base,cashier,order,account,"REFUND_PAYMENT",2001).status_code==409


def test_account_cashier_role_grants_are_admin_only(monkeypatch):
    d,org,loc,order,service,http,base,manager=prepare(monkeypatch)
    with psycopg.connect(DSN) as db:
        role=db.execute("""SELECT role_code FROM auto_organization_role_grant
          WHERE organization_id=%s AND user_id=%s AND role_code='CASHIER'
          AND revoked_at IS NULL""",(org,d["manager"])).fetchone()
        assert role==("CASHIER",)
    # CASHIER cannot create an account or use the former MANAGER-only synthetic reconciliation.
    cashier=session(http,d,d["manager"])
    assert create(http,base,cashier).status_code==403
    demo=f"/api/v1/dev/auto/organizations/{org}/locations/{loc}/orders/{order}/demo-close"
    assert http.get(demo).status_code==403
