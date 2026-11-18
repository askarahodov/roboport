"""Реальная PG17 цепочка ставки, труда, материалов и технического закрытия."""
from concurrent.futures import ThreadPoolExecutor
from io import StringIO
import csv
from threading import Barrier
from uuid import UUID,uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient

from roboport_api import auto_internal_order_costs as module
from roboport_api.main import app
from test_pg_auto_closed_order_materials_http import fixture
from test_pg_auto_document_access_http import set_rule,url as policy_url
from test_pg_auto_stock_service import grant
from test_pg_auto_work_order_estimate_http import token
from test_pg_baseline import DSN


def setup(monkeypatch,*,rated=True,mode="traced"):
    rate={}
    def before_labor(db,d,org,loc,order):
        grant(db,d["other"],org,loc,"MANAGER")
        if rated:
            state=module.propose(db,d["tokens"][d["intake"]],org,loc,uuid4(),0,2001)
            ident=UUID(state["items"][0]["id"])
            approved=module.approve(db,d["tokens"][d["other"]],org,loc,ident,uuid4())
            rate.update(revisionId=str(ident),approvalId=approved["active"]["approvalId"],
                        version=1,rateMinorPerHour=2001)
    d,org,loc,order,evidence,path,query=fixture(monkeypatch,mode,before_labor)
    root=path.split("/reports/")[0]
    return d,org,loc,order,evidence,root,query,rate


def test_complete_cost_sources_rounding_negative_difference_and_frozen_rate(monkeypatch):
    d,org,loc,order,evidence,root,query,rate=setup(monkeypatch)
    http=TestClient(app,base_url="https://127.0.0.1")
    path=root+"/reports/internal-order-costs"
    policies=root+"/internal-labor-cost-policies"
    assert http.get(path,params=query).status_code==403
    token(http,d,d["client_a"])
    assert http.get(path,params=query).status_code==403
    assert http.get(policies).status_code==403
    head=token(http,d,d["intake"])
    res=http.get(path,params=query)
    assert res.status_code==200,res.text
    data=res.json()
    assert data["summary"]==dict(orderCount=1,completeOrderCount=1,laborEntryCount=1,
      approvedTotalMinor=2500,materialNetMinor=1250,laborCostMinor=1501,
      directCostMinor=2751,syntheticDifferenceMinor=-251)
    row=data["items"][0]
    assert row["workOrderId"]==str(order)
    assert row["laborEntries"][0]["source"]==rate
    assert row["laborEntries"][0]["actualMinutes"]==45
    assert row["materialGroups"][0]["source"]["valuationId"]==str(evidence["valuation"])
    assert all(data[f] is False for f in ["realMoney","marginAvailable","payrollAvailable","releaseAllowed"])
    assert str(d["manager"]) not in res.text and str(d["client_a"]) not in res.text
    out=http.get(path+"/export",params=query)
    assert out.status_code==200 and out.headers["x-content-type-options"]=="nosniff"
    lines=list(csv.reader(StringIO(out.text.lstrip("\ufeff")),delimiter=";"))
    assert len(lines)==6 and all(len(line)==29 for line in lines)
    assert lines[1][4:9]==["2500","1250","1501","2751","-251"]
    assert lines[2][12:16]==[rate["revisionId"],rate["approvalId"],"1","2001"]
    # Новый revision не меняет snapshot старой записи.
    payload={"requestKey":str(uuid4()),"expectedVersion":1,"rateMinorPerHour":6000}
    assert http.post(policies,json=payload).status_code==403
    assert http.post(policies,headers=head,json={**payload,"rateMinorPerHour":True}).status_code==422
    new=http.post(policies,headers=head,json=payload)
    assert new.status_code==201,new.text
    revision=new.json()["items"][-1]["id"]
    assert http.post(policies,headers=head,json=payload).status_code==200
    assert http.post(policies,headers=head,json={**payload,"rateMinorPerHour":7000}).status_code==409
    approval={"requestKey":str(uuid4())}
    assert http.post(policies+"/"+revision+"/approve",headers=head,json=approval).status_code==403
    approver=token(http,d,d["other"])
    approved=http.post(policies+"/"+revision+"/approve",headers=approver,json=approval)
    assert approved.status_code==201,approved.text
    assert http.post(policies+"/"+revision+"/approve",headers=approver,json=approval).status_code==200
    token(http,d,d["intake"])
    assert http.get(path,params=query).json()==data
    # База не позволяет перепривязать ни старую запись, ни ставку.
    with psycopg.connect(DSN) as db:
        for sql,args in [
          ("UPDATE auto_work_order_labor_entry SET internal_cost_approval_id=%s WHERE id=%s",
           (UUID(approved.json()["active"]["approvalId"]),UUID(row["laborEntries"][0]["laborEntryId"]))),
          ("UPDATE auto_internal_labor_cost_revision SET rate_minor_per_hour=1 WHERE id=%s",
           (UUID(rate["revisionId"]),)),
        ]:
            with pytest.raises(psycopg.errors.RaiseException):
                with db.transaction():
                    db.execute(sql,args)
    assert http.get(path.replace(str(loc),str(uuid4())),params=query).status_code==403
    assert http.get(path,params={"fromDay":"2026-02-31","toDay":query["toDay"]}).status_code==422
    monkeypatch.setattr(module,"MAX_LABOR",0)
    assert http.get(path,params=query).status_code==409
    monkeypatch.setattr(module,"MAX_LABOR",500)
    # Запрет внутренних затрат не обходится ни экспортом, ни политикой.
    owner=token(http,d,d["other"])
    assert set_rule(http,policy_url(org),owner,loc=loc,user=d["intake"],
                    document="FINANCE",cap="PRICE_PURCHASE").status_code==201
    token(http,d,d["intake"])
    assert http.get(policies).status_code==403
    assert http.get(path,params=query).status_code==403
    assert http.get(path+"/export",params=query).status_code==403


def test_missing_rate_and_posthoc_approval_do_not_create_labor_cost(monkeypatch):
    d,org,loc,order,evidence,root,query,_=setup(monkeypatch,rated=False)
    http=TestClient(app,base_url="https://127.0.0.1")
    path=root+"/reports/internal-order-costs"
    token(http,d,d["intake"])
    result=http.get(path,params=query)
    assert result.status_code==200,result.text
    row=result.json()["items"][0]
    assert row["materialNetMinor"]==1250 and row["laborCostMinor"] is None
    assert row["syntheticDifferenceMinor"] is None and row["status"]=="INCOMPLETE"
    assert row["laborEntries"][0]["status"]=="NO_APPROVED_RATE"
    with psycopg.connect(DSN) as db:
        p=module.propose(db,d["tokens"][d["intake"]],org,loc,uuid4(),0,1000)
        module.approve(db,d["tokens"][d["other"]],org,loc,UUID(p["items"][0]["id"]),uuid4())
    assert http.get(path,params=query).json()==result.json()


def test_unknown_material_and_revoked_mechanic_fail_closed(monkeypatch):
    d,org,loc,order,evidence,root,query,rate=setup(monkeypatch,mode="mixed")
    http=TestClient(app,base_url="https://127.0.0.1")
    path=root+"/reports/internal-order-costs"
    token(http,d,d["intake"])
    res=http.get(path,params=query)
    assert res.status_code==200,res.text
    assert res.json()["summary"]["materialNetMinor"] is None
    assert res.json()["summary"]["laborCostMinor"]==1501
    assert res.json()["summary"]["syntheticDifferenceMinor"] is None
    with psycopg.connect(DSN) as db:
        db.execute("""UPDATE auto_organization_role_grant SET revoked_at=now()
          WHERE organization_id=%s AND user_id=%s AND role_code='MECHANIC'
            AND revoked_at IS NULL""",(org,d["manager"]))
    row=http.get(path,params=query).json()["items"][0]
    assert row["laborEntries"][0]["status"]=="INACTIVE_MECHANIC"
    assert row["laborEntries"][0]["source"] is None
    assert row["laborCostMinor"] is None and row["directCostMinor"] is None


def test_concurrent_rate_proposals_one_winner(monkeypatch):
    d,org,loc,order,evidence,root,query,_=setup(monkeypatch,rated=False)
    barrier=Barrier(2)
    def attempt(actor):
        barrier.wait(timeout=15)
        try:
            with psycopg.connect(DSN) as db:
                module.propose(db,d["tokens"][actor],org,loc,uuid4(),0,900)
            return "CREATED"
        except module.Conflict:
            return "CONFLICT"
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(attempt,[d["intake"],d["other"]]))==["CONFLICT","CREATED"]
    with psycopg.connect(DSN) as db:
        assert db.execute("SELECT count(*) FROM auto_internal_labor_cost_revision WHERE location_id=%s",
                          (loc,)).fetchone()==(1,)


def test_finance_preview_and_dual_role_cost_denial_match_real_api(monkeypatch):
    d,org,loc,order,evidence,root,query,_=setup(monkeypatch)
    http=TestClient(app,base_url="https://127.0.0.1")
    path=root+"/reports/internal-order-costs"
    policies=root+"/internal-labor-cost-policies"
    # CASHIER без MANAGER не имеет доступа даже к ставке филиала.
    with psycopg.connect(DSN) as db:
        grant(db,d["manager"],org,loc,"CASHIER")
        grant(db,d["intake"],org,loc,"CASHIER")
    owner=token(http,d,d["other"])
    def preview(user):
        res=http.get(policy_url(org)+"/effective",params={
            "locationId":str(loc),"userId":str(user),"documentKind":"FINANCE"})
        assert res.status_code==200,res.text
        return res.json()["capabilities"]["PRICE_PURCHASE"]["allowed"]
    assert preview(d["intake"]) is True and preview(d["manager"]) is False
    token(http,d,d["manager"])
    assert http.get(policies).status_code==403
    owner=token(http,d,d["other"])
    assert set_rule(http,policy_url(org),owner,role="CASHIER",loc=loc,
                    document="FINANCE",cap="PRICE_PURCHASE").status_code==201
    assert preview(d["intake"]) is False
    token(http,d,d["intake"])
    assert http.get(policies).status_code==403
    assert http.get(path,params=query).status_code==403
    assert http.get(path+"/export",params=query).status_code==403


def test_stale_mechanic_and_owner_proof_snapshot_cannot_reveal_cost(monkeypatch):
    d,org,loc,order,evidence,root,query,_=setup(monkeypatch)
    from roboport_api.auto_closed_order_report import report as closed_report
    # Старая MVCC-версия допуска механика не даёт положительную оценку
    # после того, как отзыв уже committed в другой транзакции.
    with psycopg.connect(DSN) as reader:
        with pytest.raises(psycopg.errors.SerializationFailure):
            with reader.transaction():
                reader.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
                assert reader.execute("SELECT count(*) FROM auto_work_order_labor_entry").fetchone()[0]>0
                with psycopg.connect(DSN) as writer:
                    writer.execute("""UPDATE auto_organization_role_grant SET revoked_at=now()
                      WHERE organization_id=%s AND user_id=%s AND role_code='MECHANIC'
                      AND revoked_at IS NULL""",(org,d["manager"]))
                module._load_labor(reader,[order],org,loc)
    # Отдельно владелец: снимок до отзыва proof не может вернуть закрытый заказ.
    with psycopg.connect(DSN) as reader:
        with pytest.raises(psycopg.errors.SerializationFailure):
            with reader.transaction():
                reader.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
                reader.execute("SELECT id FROM auto_vehicle_customer_link WHERE id=%s",(d["link"],)).fetchone()
                with psycopg.connect(DSN) as writer:
                    writer.execute("UPDATE auto_vehicle_customer_link SET revoked_at=now() WHERE id=%s",(d["link"],))
                closed_report(reader,d["tokens"][d["intake"]],org,loc,query["fromDay"],query["toDay"])
    http=TestClient(app,base_url="https://127.0.0.1")
    token(http,d,d["intake"])
    res=http.get(root+"/reports/internal-order-costs",params=query)
    assert res.status_code==200,res.text
    assert res.json()["items"]==[]


def test_fifo_material_cost_flows_into_complete_internal_cost_report(monkeypatch):
    d,org,loc,order,evidence,root,query,rate=setup(monkeypatch,mode="fifo")
    http=TestClient(app,base_url="https://127.0.0.1")
    token(http,d,d["intake"])
    path=root+"/reports/internal-order-costs"
    res=http.get(path,params=query)
    assert res.status_code==200,res.text
    data=res.json()
    assert data["summary"]==dict(orderCount=1,completeOrderCount=1,laborEntryCount=1,
        approvedTotalMinor=2500,materialNetMinor=5750,laborCostMinor=1501,
        directCostMinor=7251,syntheticDifferenceMinor=-4751)
    group=data["items"][0]["materialGroups"][0]
    assert group["status"]=="FIFO_TRACED" and len(group["fifoSlices"])==3
    out=http.get(path+"/export",params=query)
    assert out.status_code==200,out.text
    lines=list(csv.reader(StringIO(out.text.lstrip("\ufeff")),delimiter=";"))
    assert len(lines)==9 and all(len(line)==29 for line in lines)
    slices=[line for line in lines if line[2]=="FIFO_SLICE"]
    assert sorted(int(line[5]) for line in slices)==[-2000,3750,4000]
    assert {line[-1] for line in slices}=={str(evidence["issue"])}
