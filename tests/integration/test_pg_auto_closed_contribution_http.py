"""Real PG17/HTTPS: immutable QC close, cash preview, traced labor/materials."""
from io import StringIO
from uuid import UUID,uuid4
import csv
import psycopg
from fastapi.testclient import TestClient
from roboport_api.main import app
from roboport_api import auto_closed_contribution as module
from roboport_api import auto_internal_order_costs as costs
from test_pg_auto_internal_order_costs_http import setup
from test_pg_auto_document_access_http import set_rule,url as policy_url
from test_pg_auto_work_order_estimate_http import token
from test_pg_baseline import DSN


def _path(root):
    return root+"/reports/closed-contribution"


def test_full_qc_closed_order_sim_settlement_material_labor_contribution_csv(monkeypatch):
    d,org,loc,order,evidence,root,query,rate=setup(monkeypatch)
    http=TestClient(app,base_url="https://127.0.0.1")
    path=_path(root)
    assert http.get(path,params=query).status_code==403
    token(http,d,d["client_a"])
    assert http.get(path,params=query).status_code==403
    assert http.get(path+"/export",params=query).status_code==403
    token(http,d,d["intake"])
    result=http.get(path,params=query)
    assert result.status_code==200,result.text
    assert result.headers["cache-control"]=="no-store"
    data=result.json()
    assert (data["syntheticOnly"],data["realMoney"],data["marginAvailable"],
            data["payrollAvailable"],data["releaseAllowed"])==(True,False,False,False,False)
    assert data["basis"]==module.BASIS
    assert data["summary"]["orderCount"]==1
    assert data["summary"]["completeOrderCount"]==1
    assert data["summary"]["incompleteOrderCount"]==0
    assert data["summary"]["approvedTotalMinor"]==2500
    assert data["summary"]["simulatedNetMinor"]==2500
    assert data["summary"]["materialNetMinor"]==1250
    assert data["summary"]["laborCostMinor"]==1501
    assert data["summary"]["directCostMinor"]==2751
    assert data["summary"]["syntheticDifferenceMinor"]==-251
    assert len(data["days"])==1
    assert data["days"][0]["localDay"]==query["fromDay"]
    assert data["days"][0]["syntheticDifferenceMinor"]==-251
    row=data["items"][0]
    assert row["workOrderId"]==str(order) and row["status"]=="COMPLETE"
    assert UUID(row["closedEventId"]) and UUID(row["postedEventId"]) and UUID(row["ackEventId"])
    assert UUID(row["estimateRevisionId"]) and UUID(row["previewId"])
    assert row["laborEntries"][0]["source"]==rate
    assert row["laborEntries"][0]["actualMinutes"]==45
    assert row["materialGroups"][0]["source"]["valuationId"]==str(evidence["valuation"])
    assert row["simulatedPaidMinor"]-row["simulatedRefundedMinor"]==2500
    events=row["cashEvents"]
    assert events and all(e["kind"] in ("SIM_PAYMENT","SIM_REFUND") for e in events)
    with psycopg.connect(DSN) as db:
        dbids={str(v[0]) for v in db.execute("""
            SELECT id FROM auto_order_demo_cash_event WHERE work_order_id=%s
        """,(order,)).fetchall()}
    assert {e["id"] for e in events}==dbids
    assert data["summary"]["simCashEventCount"]==len(dbids)
    assert data["summary"]["laborEntryCount"]==1
    assert str(d["client_a"]) not in result.text
    assert str(d["manager"]) not in result.text
    export=http.get(path+"/export",params=query)
    assert export.status_code==200,export.text
    assert export.headers["cache-control"]=="no-store"
    assert export.headers["x-content-type-options"]=="nosniff"
    assert export.headers["content-type"].startswith("text/csv")
    rows=list(csv.reader(StringIO(export.text.lstrip("\ufeff")),delimiter=";"))
    assert all(len(r)==16 for r in rows)
    assert rows[0][0]=="День"
    assert len(rows)==1+1+1+len(events)+len(row["laborEntries"])+sum(
        len(g["movements"]) for g in row["materialGroups"])
    assert {r[1] for r in rows[1:]}=={"DAY","ORDER","SIM_CASH","LABOR","STOCK"}
    assert {r[3] for r in rows[1:] if r[1]=="SIM_CASH"}==dbids
    assert next(r for r in rows if r[1]=="ORDER")[11]=="-251"
    assert str(d["client_a"]) not in export.text
    assert http.get(path.replace(str(org),str(uuid4())),params=query).status_code==403
    assert http.get(path.replace(str(loc),str(uuid4())),params=query).status_code==403
    assert http.get(path,params={"fromDay":"2026-02-30","toDay":query["toDay"]}).status_code==422
    assert http.get(path,params={"fromDay":"2025-01-01","toDay":query["toDay"]}).status_code==422
    monkeypatch.setattr(costs,"MAX_LABOR",0)
    assert http.get(path,params=query).status_code==409
    assert http.get(path+"/export",params=query).status_code==409
    monkeypatch.setattr(costs,"MAX_LABOR",500)
    # Fresh FINANCE purchase price DENY prevents BOTH JSON and CSV.
    owner=token(http,d,d["other"])
    assert set_rule(http,policy_url(org),owner,loc=loc,user=d["intake"],
                    document="FINANCE",cap="PRICE_PURCHASE").status_code==201
    token(http,d,d["intake"])
    assert http.get(path,params=query).status_code==403
    assert http.get(path+"/export",params=query).status_code==403


def test_missing_cost_source_never_becomes_zero_or_invented_profit(monkeypatch):
    d,org,loc,order,evidence,root,query,_=setup(monkeypatch,rated=False)
    http=TestClient(app,base_url="https://127.0.0.1")
    path=_path(root)
    token(http,d,d["intake"])
    result=http.get(path,params=query)
    assert result.status_code==200,result.text
    data=result.json()
    assert data["summary"]["orderCount"]==1
    assert data["summary"]["completeOrderCount"]==0
    assert data["summary"]["incompleteOrderCount"]==1
    assert data["summary"]["approvedTotalMinor"]==2500
    assert data["summary"]["simulatedNetMinor"]==2500
    assert data["summary"]["materialNetMinor"] is None
    assert data["summary"]["directCostMinor"] is None
    assert data["summary"]["syntheticDifferenceMinor"] is None
    item=data["items"][0]
    assert item["status"]=="INCOMPLETE"
    assert item["materialNetMinor"]==1250
    assert item["laborCostMinor"] is None
    assert item["syntheticDifferenceMinor"] is None
    assert item["laborEntries"][0]["status"]=="NO_APPROVED_RATE"
    csv_text=http.get(path+"/export",params=query).text
    records=list(csv.reader(StringIO(csv_text.lstrip("\ufeff")),delimiter=";"))
    day=next(r for r in records if r[1]=="DAY")
    order=next(r for r in records if r[1]=="ORDER")
    assert day[10]=="" and day[11]=="" and order[11]==""
    # Even after approving a new cost rate old labor is not silently repriced.
    with psycopg.connect(DSN) as db:
        proposal=costs.propose(db,d["tokens"][d["intake"]],org,loc,uuid4(),0,1000)
        costs.approve(db,d["tokens"][d["other"]],org,loc,
                      UUID(proposal["items"][0]["id"]),uuid4())
    again=http.get(path,params=query)
    assert again.status_code==200 and again.json()==data


def test_unvalued_material_and_revoked_mechanic_exclude_difference(monkeypatch):
    d,org,loc,order,evidence,root,query,_=setup(monkeypatch,mode="mixed")
    http=TestClient(app,base_url="https://127.0.0.1")
    path=_path(root)
    token(http,d,d["intake"])
    result=http.get(path,params=query)
    assert result.status_code==200,result.text
    assert result.json()["items"][0]["materialNetMinor"] is None
    assert result.json()["summary"]["syntheticDifferenceMinor"] is None
    with psycopg.connect(DSN) as db:
        db.execute("""UPDATE auto_organization_role_grant SET revoked_at=now()
            WHERE organization_id=%s AND user_id=%s
              AND role_code='MECHANIC' AND revoked_at IS NULL
        """,(org,d["manager"]))
    result=http.get(path,params=query)
    assert result.status_code==200
    item=result.json()["items"][0]
    assert item["laborEntries"][0]["status"]=="INACTIVE_MECHANIC"
    assert item["laborCostMinor"] is None
    assert item["syntheticDifferenceMinor"] is None


def test_empty_close_period_is_zero_not_margin_or_payroll(monkeypatch):
    d,org,loc,order,evidence,root,query,_=setup(monkeypatch)
    http=TestClient(app,base_url="https://127.0.0.1")
    token(http,d,d["intake"])
    path=_path(root)
    empty=http.get(path,params={"fromDay":"2026-01-01","toDay":"2026-01-02"})
    assert empty.status_code==200,empty.text
    obj=empty.json()
    assert obj["summary"]["orderCount"]==0
    assert obj["summary"]["approvedTotalMinor"]==0
    assert obj["summary"]["syntheticDifferenceMinor"]==0
    assert obj["summary"]["simCashEventCount"]==0
    assert len(obj["days"])==2
    assert all(x["orderCount"]==0 for x in obj["days"])
    assert obj["realMoney"] is False and obj["marginAvailable"] is False
