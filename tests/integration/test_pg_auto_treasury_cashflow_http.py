"""PG17/HTTPS: verified branch cashflow is the sum of immutable cashbooks."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from io import StringIO
from uuid import UUID, uuid4
import csv

import psycopg
import pytest
from fastapi.testclient import TestClient

from roboport_api import auto_treasury_cashflow as module
from roboport_api.auto_treasury import (
    TreasuryConflict, record_event,
)
from roboport_api.main import app
from test_pg_auto_treasury_http import (
    prepare, create, event, complete_qc, local_day, session,
)
from test_pg_baseline import DSN


def test_full_branch_cashflow_two_accounts_reconciled_events_and_csv(monkeypatch):
    d,org,loc,order,service,http,base,manager=prepare(monkeypatch)
    report=base+"/cashflow"
    opened=create(http,base,manager)
    assert opened.status_code==201,opened.text
    cash=UUID(opened.json()["accountId"])
    bank_resp=http.post(base+"/accounts",headers=manager,json={
        "requestKey":str(uuid4()),"code":"BANK-01",
        "label":"Тестовый счёт 02","kind":"BANK_SIM",
    })
    assert bank_resp.status_code==201,bank_resp.text
    bank=UUID(bank_resp.json()["accountId"])
    with psycopg.connect(DSN) as db:
        today=local_day(db,loc)
        start=(date.fromisoformat(today)-timedelta(days=1)).isoformat()
        end=(date.fromisoformat(today)+timedelta(days=1)).isoformat()
    query={"fromDay":start,"toDay":end}
    http.cookies.clear()
    assert http.get(report,params=query).status_code==403
    session(http,d,d["client_a"])
    assert http.get(report,params=query).status_code==403
    assert http.get(report+"/export",params=query).status_code==403
    cashier=session(http,d,d["manager"])
    assert event(http,base,cashier,order,cash,"ADVANCE",900).status_code==201
    assert event(http,base,cashier,order,cash,"REFUND_ADVANCE",200).status_code==201
    assert event(http,base,cashier,order,bank,"ADVANCE",600).status_code==201
    complete_qc(d,org,loc,order,service)
    assert event(http,base,cashier,order,cash,"APPLY_ADVANCE",400).status_code==201
    assert event(http,base,cashier,order,bank,"PAYMENT",400).status_code==201
    assert event(http,base,cashier,order,bank,"REFUND_PAYMENT",100).status_code==201
    for ident in (cash,bank):
        close=http.post(base+f"/accounts/{ident}/close-day",headers=cashier,json={
            "requestKey":str(uuid4()),"businessDay":today,
        })
        assert close.status_code==201,close.text
    res=http.get(report,params=query)
    assert res.status_code==200,res.text
    assert res.headers["cache-control"]=="no-store"
    result=res.json()
    assert result["syntheticOnly"] is True
    assert result["realMoney"] is False
    assert result["releaseAllowed"] is False
    assert result["basis"]==module.BASIS
    assert result["summary"]=={
        "accountCount":2,"eventCount":6,"sealedAccountDayCount":2,
        "openingMinor":"0","incomingMinor":"1900","outgoingMinor":"300",
        "internalAppliedMinor":"400","closingMinor":"1600",
    }
    assert len(result["days"])==3
    middle=result["days"][1]
    assert middle=={
        "localDay":today,"openingMinor":"0","incomingMinor":"1900",
        "outgoingMinor":"300","internalAppliedMinor":"400",
        "closingMinor":"1600","eventCount":6,"sealedAccountCount":2,
    }
    assert result["days"][0]["closingMinor"]=="0"
    assert result["days"][2]["openingMinor"]=="1600"
    by_id={a["accountId"]:a for a in result["accounts"]}
    assert len(by_id)==2
    assert by_id[str(cash)]["summary"]["closingMinor"]=="700"
    assert by_id[str(bank)]["summary"]["closingMinor"]=="900"
    assert len(by_id[str(cash)]["days"][1]["events"])==3
    assert len(by_id[str(bank)]["days"][1]["events"])==3
    event_ids={ev["eventId"] for account in result["accounts"]
               for day in account["days"] for ev in day["events"]}
    assert len(event_ids)==6
    with psycopg.connect(DSN) as db:
        sources=db.execute("""SELECT id FROM auto_treasury_event
          WHERE account_id=ANY(%s::uuid[])""",([cash,bank],)).fetchall()
    assert event_ids=={str(row[0]) for row in sources}
    # Source payload never includes WorkOrder, source reason, customer,
    # real bank account info or employee PII.
    assert str(order) not in res.text
    assert str(d["client_a"]) not in res.text
    assert "Тестовый акт оплаты" not in res.text
    exported=http.get(report+"/export",params=query)
    assert exported.status_code==200,exported.text
    assert exported.headers["cache-control"]=="no-store"
    assert exported.headers["x-content-type-options"]=="nosniff"
    assert exported.headers["content-type"].startswith("text/csv")
    records=list(csv.reader(StringIO(exported.text.lstrip("\ufeff")),delimiter=";"))
    assert all(len(row)==16 for row in records)
    assert len(records)==1+3+6+4  # header + 3 branch days + 6 events + 4 empty account days
    assert {row[1] for row in records[1:]}=={"BRANCH","ACCOUNT_EVENT","ACCOUNT_DAY"}
    assert {row[5] for row in records if row[5]}==event_ids
    assert sum(row[6]=="APPLY_ADVANCE" for row in records)==1
    assert str(order) not in exported.text
    assert "Тестовый акт оплаты" not in exported.text
    assert str(d["client_a"]) not in exported.text
    assert http.get(report.replace(str(loc),str(uuid4())),params=query).status_code==403
    assert http.get(report.replace(str(org),str(uuid4())),params=query).status_code==403
    assert http.get(report,params={"fromDay":"2026-02-31","toDay":today}).status_code==422
    assert http.get(report,params={"fromDay":today,"toDay":"2025-01-01"}).status_code==422
    assert http.get(report,params={"fromDay":"2025-01-01","toDay":today}).status_code==422
    # No silent truncation: overflow rejects both JSON and CSV completely.
    monkeypatch.setattr(module,"MAX_EVENTS",5)
    assert http.get(report,params=query).status_code==409
    assert http.get(report+"/export",params=query).status_code==409
    monkeypatch.setattr(module,"MAX_EVENTS",1000)
    monkeypatch.setattr(module,"MAX_ACCOUNTS",1)
    assert http.get(report,params=query).status_code==409
    assert http.get(report+"/export",params=query).status_code==409
    monkeypatch.setattr(module,"MAX_ACCOUNTS",50)
    # Current CASHIER grant controls both paths; revoked users get 403.
    with psycopg.connect(DSN) as db:
        db.execute("""UPDATE auto_organization_role_grant SET revoked_at=now()
           WHERE organization_id=%s AND user_id=%s
           AND role_code='CASHIER' AND revoked_at IS NULL
        """,(org,d["manager"]))
    assert http.get(report,params=query).status_code==403
    assert http.get(report+"/export",params=query).status_code==403
    session(http,d,d["intake"])
    assert http.get(report,params=query).status_code==200


def test_cashflow_no_accounts_is_31_day_bounded_zero_and_no_sources(monkeypatch):
    d,org,loc,order,service,http,base,manager=prepare(monkeypatch)
    today="2026-10-25"
    response=http.get(base+"/cashflow",params={"fromDay":today,"toDay":today})
    assert response.status_code==200,response.text
    body=response.json()
    assert body["accounts"]==[]
    assert body["summary"]=={
        "accountCount":0,"eventCount":0,"sealedAccountDayCount":0,
        "openingMinor":"0","incomingMinor":"0","outgoingMinor":"0",
        "internalAppliedMinor":"0","closingMinor":"0",
    }
    assert body["days"]==[{
        "localDay":today,"openingMinor":"0","incomingMinor":"0",
        "outgoingMinor":"0","internalAppliedMinor":"0",
        "closingMinor":"0","eventCount":0,"sealedAccountCount":0,
    }]
    export=http.get(base+"/cashflow/export",params={
        "fromDay":today,"toDay":today})
    assert export.status_code==200
    rows=list(csv.reader(StringIO(export.text.lstrip("\ufeff")),delimiter=";"))
    assert len(rows)==2 and rows[1][1]=="BRANCH"


def test_cashflow_rejects_csv_formula_and_has_no_partial_export(monkeypatch):
    result={"days":[],"accounts":[{
        "accountId":str(uuid4()),"accountCode":"=HYPERLINK(1)",
        "accountKind":"CASH_SIM","days":[]
    }]}
    with pytest.raises(TreasuryConflict):
        module._csv_export(result)
    # Even an otherwise valid body larger than the safe export bound is
    # rejected before the Content-Type can become text/csv.
    monkeypatch.setattr(module,"MAX_EXPORT_BYTES",4)
    with pytest.raises(TreasuryConflict):
        module._csv_export({"days":[],"accounts":[]})


@pytest.mark.parametrize("error", [
    psycopg.errors.SerializationFailure,
    psycopg.errors.DeadlockDetected,
])
def test_cashflow_snapshot_revocation_conflict_maps_to_409(monkeypatch,error):
    monkeypatch.setattr(module,"_request_dsn",lambda request:"unused")
    def fail_connect(dsn):
        raise error("forced concurrent grant change")
    monkeypatch.setattr(module.psycopg,"connect",fail_connect)
    http=TestClient(app,base_url="https://127.0.0.1")
    route=(f"/api/v1/dev/auto/organizations/{uuid4()}"
           f"/locations/{uuid4()}/treasury/cashflow")
    query={"fromDay":"2026-11-18","toDay":"2026-11-18"}
    for suffix in ("","/export"):
        response=http.get(route+suffix,params=query)
        assert response.status_code==409
        assert response.headers["content-type"].startswith("application/json")
        assert "REPORT_CONFLICT" in response.text
        assert "text/csv" not in response.headers["content-type"]
