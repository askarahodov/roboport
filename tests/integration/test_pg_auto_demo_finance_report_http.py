"""PG17/HTTPS: reconcile two *independent* synthetic ledger event streams."""
from datetime import timedelta
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient

from roboport_api.main import app
from roboport_api.auto_demo_close import (
    simulate_cash, seal_handover_preview, DemoCloseConflict,
)
from roboport_api.auto_order_financial_post import post_work_order_financial
from roboport_api.auto_stock_service import AutoStockConflict
from test_pg_auto_demo_close_http import ready
from test_pg_auto_bay_booking import _org_and_bay
from test_pg_baseline import DSN


def url(org,loc):
    return f"/api/v1/dev/auto/organizations/{org}/locations/{loc}/demo-finance-report"


def test_two_journals_replay_refund_seal_and_access_are_independent(monkeypatch):
    d,org,loc,order,*_=ready(monkeypatch,with_material=False)
    manager=d["tokens"][d["intake"]]
    http=TestClient(app,base_url="https://127.0.0.1")
    path=url(org,loc)
    with psycopg.connect(DSN) as db:
        zone=db.execute(
            "SELECT timezone_name FROM auto_service_location WHERE id=%s",
            (loc,),
        ).fetchone()[0]
    from zoneinfo import ZoneInfo
    from datetime import datetime, timezone
    day=datetime.now(timezone.utc).astimezone(ZoneInfo(zone)).date().isoformat()
    params={"fromDay":day,"toDay":day}
    assert http.get(path,params=params).status_code==403
    http.cookies.set("rp_session",manager)
    empty=http.get(path,params=params)
    assert empty.status_code==200,empty.text
    assert empty.json()["financeCurrency"] is None
    assert empty.json()["demoCurrency"]=="RUB"
    assert empty.json()["releaseAllowed"] is False
    assert empty.json()["legalGatePassed"] is False
    assert empty.json()["items"][0]["correctionNetMinor"]==0
    assert empty.json()["items"][0]["demoNetMinor"]==0

    with psycopg.connect(DSN) as db:
        charge_key=uuid4()
        charge=post_work_order_financial(db,manager,org,loc,order,
                                         charge_key,"CHARGE",4500,"demo charge")
        assert post_work_order_financial(db,manager,org,loc,order,
                                         charge_key,"CHARGE",4500,"demo charge")==charge
        post_work_order_financial(db,manager,org,loc,order,
                                  uuid4(),"CREDIT",1200,"demo credit")
        with pytest.raises(AutoStockConflict):
            post_work_order_financial(db,manager,org,loc,order,
                                      uuid4(),"CREDIT",9000,"too much credit")
        pay_key=uuid4()
        first=simulate_cash(db,manager,org,loc,order,
                            request_key=pay_key,kind="SIM_PAYMENT",amount_minor=2500)
        assert first["replayed"] is False
        again=simulate_cash(db,manager,org,loc,order,
                            request_key=pay_key,kind="SIM_PAYMENT",amount_minor=2500)
        assert again["replayed"] is True
        simulate_cash(db,manager,org,loc,order,
                      request_key=uuid4(),kind="SIM_REFUND",amount_minor=500)
        with pytest.raises(DemoCloseConflict):
            simulate_cash(db,manager,org,loc,order,
                          request_key=uuid4(),kind="SIM_REFUND",amount_minor=2501)
        simulate_cash(db,manager,org,loc,order,
                      request_key=uuid4(),kind="SIM_PAYMENT",amount_minor=500)
        seal_key=uuid4()
        seal=seal_handover_preview(db,manager,org,loc,order,
                                  request_key=seal_key)
        assert seal["replayed"] is False
        sealed_again=seal_handover_preview(db,manager,org,loc,order,
                                           request_key=seal_key)
        assert sealed_again["replayed"] is True
        # The two series have distinct primary tables, not one ledger.
        assert db.execute(
            "SELECT COUNT(*) FROM auto_work_order_financial_event WHERE work_order_id=%s",
            (order,),
        ).fetchone()[0]==2
        assert db.execute(
            "SELECT COUNT(*) FROM auto_order_demo_cash_event WHERE work_order_id=%s",
            (order,),
        ).fetchone()[0]==3
        day=db.execute(
            "SELECT created_at FROM auto_order_demo_handover_preview WHERE work_order_id=%s",
            (order,),
        ).fetchone()[0].astimezone(ZoneInfo(zone)).date().isoformat()
    result=http.get(path,params={"fromDay":day,"toDay":day})
    assert result.status_code==200,result.text
    assert result.headers["cache-control"]=="no-store"
    data=result.json()
    assert data["syntheticOnly"] and data["financeCurrency"] is None
    assert data["demoCurrency"]=="RUB" and data["timeZone"]==zone
    row=data["items"][0]
    assert row["chargeEvents"]==1 and row["chargeMinor"]==4500
    assert row["creditEvents"]==1 and row["creditMinor"]==1200
    assert row["correctionNetMinor"]==3300
    assert row["demoPaymentEvents"]==2 and row["demoPaymentMinor"]==3000
    assert row["demoRefundEvents"]==1 and row["demoRefundMinor"]==500
    assert row["demoNetMinor"]==2500
    assert row["sealedPreviewCount"]==1
    assert "customerId" not in result.text and "actorId" not in result.text
    assert str(order) not in result.text and str(d["vehicle"]) not in result.text

    with psycopg.connect(DSN) as db:
        foreign,foreign_loc,_=_org_and_bay(db)
    assert http.get(url(foreign,foreign_loc),params={
        "fromDay":day,"toDay":day,
    }).status_code==403
    http.cookies.set("rp_session",d["tokens"][d["client_a"]])
    assert http.get(path,params={"fromDay":day,"toDay":day}).status_code==403
    http.cookies.set("rp_session",manager)
    with psycopg.connect(DSN) as db:
        db.execute(
            """UPDATE auto_organization_role_grant SET revoked_at=now()
               WHERE organization_id=%s AND location_id=%s
                 AND user_id=%s AND role_code='MANAGER'""",
            (org,loc,d["intake"]),
        )
    assert http.get(path,params={"fromDay":day,"toDay":day}).status_code==403


def test_report_validation_dst_dates_and_synthetic_only(monkeypatch):
    d,org,loc,order,*_=ready(monkeypatch,with_material=False)
    http=TestClient(app,base_url="https://127.0.0.1")
    http.cookies.set("rp_session",d["tokens"][d["intake"]])
    path=url(org,loc)
    with psycopg.connect(DSN) as db:
        # The zone must be set BEFORE scheduling; do not bypass the DB guard.
        existing=db.execute(
            "SELECT timezone_name FROM auto_service_location WHERE id=%s",
            (loc,),
        ).fetchone()[0]
    for first,last in [
        ("bad","2030-10-27"),("2030-01-02","2030-01-01"),
        ("2030-01-01","2030-02-01"),("2030-02-30","2030-03-01"),
        ("9999-12-31","9999-12-31"),
    ]:
        assert http.get(path,params={
            "fromDay":first,"toDay":last,
        }).status_code==422
    assert http.get(path.replace(str(org),"not-uuid"),params={
        "fromDay":"2030-10-27","toDay":"2030-10-27",
    }).status_code==422
    clean=http.get(path,params={
        "fromDay":"2030-10-27","toDay":"2030-10-28",
    })
    assert clean.status_code==200,clean.text
    assert [x["localDay"] for x in clean.json()["items"]]==[
        "2030-10-27","2030-10-28",
    ]
    assert all(x["demoPaymentMinor"]==0 and x["chargeMinor"]==0
               for x in clean.json()["items"])
    monkeypatch.delenv("ROBOport_SYNTHETIC_AUTO_API")
    assert http.get(path,params={
        "fromDay":"2030-10-27","toDay":"2030-10-27",
    }).status_code==404
