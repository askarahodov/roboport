"""PG17/HTTPS completion of document, price, discount and history ACL (#446).

All requests use real seeded sessions, branch grants, immutable DB policies and
actual services. No monkeypatched permission functions, no real financial data.
"""
from uuid import UUID, uuid4
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import psycopg
from fastapi.testclient import TestClient
from roboport_api.main import app
from roboport_api.auto_stock_service import AutoStockDenied
from roboport_api.auto_stock_receipt_document import pending_receipt_reviews
from roboport_api.auto_stock_receipt_valuation import record_receipt_valuation
from test_pg_baseline import DSN
from test_pg_auto_document_access_http import admin, set_rule, url as policy_url
from test_pg_auto_work_order_additional_http import (
    setup as extra_setup, extra_path, quote as extra_quote,
)
from test_pg_auto_work_order_estimate_http import api, token
from test_pg_auto_treasury_http import (
    prepare as treasury_prepare, create as treasury_create,
    event as treasury_event,
)
from test_pg_auto_work_order_execution_http import (
    fixture as execution_fixture, url as execution_url,
)
from test_pg_auto_stock_receipt_valuation import (
    setup_receipt, value as value_receipt,
)
from test_pg_auto_vehicle_claim_http import _scenario
from test_pg_auto_work_tariffs_http import setup as tariff_setup, policy_url as tariff_url, proposal
from test_pg_auto_stock_service import grant


def test_finance_cashier_template_deny_not_overridden_by_personal_allow(monkeypatch):
    d,org,loc,order,service,http,base,_=treasury_prepare(monkeypatch)
    with psycopg.connect(DSN) as db:
        admin(db,org,d["other"])
    owner=token(http,d,d["other"])
    policies=policy_url(org)
    assert http.get(policies).status_code==200
    manager=token(http,d,d["intake"])
    created=treasury_create(http,base,manager)
    assert created.status_code==201,created.text
    account=UUID(created.json()["accountId"])
    cashier=token(http,d,d["manager"])
    assert http.get(base+"/accounts").status_code==200
    # An active role-level PRICE_SALES denial beats the user's ALLOW.
    owner=token(http,d,d["other"])
    assert set_rule(http,policies,owner,role="CASHIER",loc=loc,
                    document="FINANCE",cap="PRICE_SALES").status_code==201
    assert set_rule(http,policies,owner,role="CASHIER",loc=loc,
                    user=d["manager"],document="FINANCE",
                    cap="PRICE_SALES",allowed=True).status_code==201
    preview=http.get(policies+"/effective",params={
        "locationId":str(loc),"userId":str(d["manager"]),
        "documentKind":"FINANCE"})
    assert preview.status_code==200,preview.text
    assert preview.json()["capabilities"]["PRICE_SALES"]["allowed"] is False
    cashier=token(http,d,d["manager"])
    assert http.get(base+"/accounts").status_code==403
    assert http.get(base+f"/orders/{order}").status_code==403
    assert treasury_event(http,base,cashier,order,account,"ADVANCE",100).status_code==403
    assert http.get(base+"/report",params={"fromDay":"2030-01-01",
                                           "toDay":"2030-01-01"}).status_code==403

    owner=token(http,d,d["other"])
    assert set_rule(http,policies,owner,role="CASHIER",loc=loc,
                    document="FINANCE",cap="PRICE_SALES",allowed=True).status_code==201
    cashier=token(http,d,d["manager"])
    assert http.get(base+"/accounts").status_code==200
    assert treasury_event(http,base,cashier,order,account,"ADVANCE",100).status_code==201

    # WRITE is a cross-operation block; a scoped EDIT ALLOW cannot bypass it.
    owner=token(http,d,d["other"])
    assert set_rule(http,policies,owner,role="CASHIER",loc=loc,
                    document="FINANCE",cap="WRITE").status_code==201
    assert set_rule(http,policies,owner,role="CASHIER",loc=loc,
                    user=d["manager"],document="FINANCE",
                    cap="EDIT",allowed=True).status_code==201
    cashier=token(http,d,d["manager"])
    assert treasury_event(http,base,cashier,order,account,"ADVANCE",1).status_code==403
    with psycopg.connect(DSN) as db:
        assert db.execute("SELECT count(*) FROM auto_treasury_event WHERE work_order_id=%s",
                          (order,)).fetchone()==(1,)
    owner=token(http,d,d["other"])
    assert set_rule(http,policies,owner,role="CASHIER",loc=loc,
                    document="FINANCE",cap="WRITE",allowed=True).status_code==201
    cashier=token(http,d,d["manager"])
    assert treasury_event(http,base,cashier,order,account,"ADVANCE",1).status_code==201

    # Revoked CASHIER has no read access regardless of still-published ALLOW.
    with psycopg.connect(DSN) as db:
        db.execute("""UPDATE auto_organization_role_grant SET revoked_at=now()
          WHERE organization_id=%s AND location_id=%s AND user_id=%s
          AND role_code='CASHIER'""",(org,loc,d["manager"]))
    assert http.get(base+"/accounts").status_code==403


def test_finance_manager_balance_history_correction_and_daily_report_scoped(monkeypatch):
    d,org,loc,order,service,http,base,_=treasury_prepare(monkeypatch)
    with psycopg.connect(DSN) as db:
        admin(db,org,d["other"])
    root=f"/api/v1/dev/auto/organizations/{org}/locations/{loc}"
    balance=f"{root}/orders/{order}/financial-balance"
    history=f"{root}/orders/{order}/financial-history"
    post=f"{root}/orders/{order}/financial-events"
    report=f"{root}/demo-finance-report"
    today="2030-01-01"
    manager=token(http,d,d["intake"])
    assert http.get(balance).status_code==200
    assert http.get(history).status_code==200
    assert http.get(report,params={"fromDay":today,"toDay":today}).status_code==200
    owner=token(http,d,d["other"])
    policies=policy_url(org)
    assert set_rule(http,policies,owner,loc=loc,user=d["intake"],
                    document="FINANCE",cap="PRICE_SALES").status_code==201
    manager=token(http,d,d["intake"])
    assert http.get(balance).status_code==403
    assert http.get(history).status_code==403
    assert http.get(report,params={"fromDay":today,"toDay":today}).status_code==403
    assert http.post(post,headers={**manager,"Idempotency-Key":str(uuid4())},
                     json={"action":"CHARGE","amountMinor":25,
                           "reason":"Только синтетическая корректировка"}).status_code==403
    owner=token(http,d,d["other"])
    assert set_rule(http,policies,owner,loc=loc,user=d["intake"],
                    document="FINANCE",cap="PRICE_SALES",allowed=True).status_code==201
    assert set_rule(http,policies,owner,loc=loc,user=d["intake"],
                    document="FINANCE",cap="EDIT").status_code==201
    manager=token(http,d,d["intake"])
    assert http.get(balance).status_code==200
    assert http.post(post,headers={**manager,"Idempotency-Key":str(uuid4())},
                     json={"action":"CHARGE","amountMinor":25,
                           "reason":"Только синтетическая корректировка"}).status_code==403
    assert http.get(url(org)+"/effective",params={
        "locationId":str(loc),"userId":str(d["intake"]),
        "documentKind":"FINANCE"}).status_code==403


def test_estimate_extra_price_discount_and_execution_totals_are_guarded(monkeypatch):
    d,org,loc,order,base_service,additional,base=extra_setup(monkeypatch)
    http=api(monkeypatch)
    with psycopg.connect(DSN) as db:
        admin(db,org,d["other"])
    policies=policy_url(org)
    extras=extra_path(org,loc,order)
    manager=token(http,d,d["intake"])
    assert http.get(extras).status_code==200
    owner=token(http,d,d["other"])
    assert set_rule(http,policies,owner,loc=loc,user=d["intake"],
                    document="ESTIMATE",cap="PRICE_SALES").status_code==201
    manager=token(http,d,d["intake"])
    assert http.get(extras).status_code==403
    assert http.post(extras,headers=manager,
                     json=extra_quote(additional,base)).status_code==403
    owner=token(http,d,d["other"])
    assert set_rule(http,policies,owner,loc=loc,user=d["intake"],
                    document="ESTIMATE",cap="PRICE_SALES",allowed=True).status_code==201
    assert set_rule(http,policies,owner,loc=loc,user=d["intake"],
                    document="ESTIMATE",cap="DISCOUNT").status_code==201
    manager=token(http,d,d["intake"])
    assert http.post(extras,headers=manager,
                     json=extra_quote(additional,base,discount=1000)).status_code==403
    allowed_payload=extra_quote(additional,base,discount=0)
    published=http.post(extras,headers=manager,json=allowed_payload)
    assert published.status_code==201,published.text
    assert http.get(extras).json()["items"][0]["totalMinor"]>0
    customer=token(http,d,d["client_a"])
    assert http.get(extras).status_code==200
    owner=token(http,d,d["other"])
    assert set_rule(http,policies,owner,loc=loc,user=d["intake"],
                    document="ESTIMATE",cap="READ",allowed=True,
                    not_before="2099-01-01T00:00:00Z").status_code==201
    manager=token(http,d,d["intake"])
    assert http.get(extras).status_code==403
    # Even an exact idempotent replay may not return the priced old revision.
    assert http.post(extras,headers=manager,json=allowed_payload).status_code==403
    with psycopg.connect(DSN) as db:
        assert db.execute("""SELECT COUNT(*) FROM auto_work_order_extra_revision
          WHERE work_order_id=%s""",(order,)).fetchone()==(1,)


def test_execution_response_redacts_prices_for_mechanic_and_denied_manager(monkeypatch):
    d,org,loc,order,service,_,_=execution_fixture(monkeypatch,with_material=False)
    http=TestClient(app,base_url="https://127.0.0.1")
    execution=execution_url(org,loc,order)
    manager=token(http,d,d["intake"])
    assert http.get(execution).json()["approvedTotalMinor"]==2500
    mech=token(http,d,d["manager"])
    step=http.get(execution)
    assert step.status_code==200,step.text
    assert step.json()["status"]=="APPROVED"
    assert step.json()["approvedTotalMinor"] is None
    with psycopg.connect(DSN) as db:
        admin(db,org,d["other"])
    owner=token(http,d,d["other"])
    policies=policy_url(org)
    assert set_rule(http,policies,owner,role="MANAGER",loc=loc,
                    document="ESTIMATE",cap="PRICE_SALES").status_code==201
    manager=token(http,d,d["intake"])
    response=http.get(execution)
    assert response.status_code==200,response.text
    assert response.json()["approvedTotalMinor"] is None
    assert response.json()["requiredServices"][0]["serviceId"]==str(service)
    owner=token(http,d,d["other"])
    assert set_rule(http,policies,owner,role="MANAGER",loc=loc,
                    document="ESTIMATE",cap="PRICE_SALES",allowed=True).status_code==201
    manager=token(http,d,d["intake"])
    assert http.get(execution).json()["approvedTotalMinor"]==2500


def test_receipt_queue_and_valuation_cannot_bypass_purchase_price_policy(monkeypatch):
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTO_API","1")
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTH_API","1")
    monkeypatch.setenv("ROBOport_DATABASE_URL",DSN)
    d=_scenario()
    with psycopg.connect(DSN) as db:
        org,loc,sku,lot,doc=setup_receipt(db,d,accept=True)
        admin(db,org,d["other"])
    http=TestClient(app,base_url="https://127.0.0.1")
    owner=token(http,d,d["other"])
    policies=policy_url(org)
    assert set_rule(http,policies,owner,role="MANAGER",loc=loc,
                    user=d["manager"],document="SUPPLIER_ORDER",
                    cap="PRICE_PURCHASE").status_code==201
    with psycopg.connect(DSN) as db:
        try:
            pending_receipt_reviews(db,d["tokens"][d["manager"]],org,loc,"value")
            assert False,"supposed to deny"
        except AutoStockDenied:
            pass
        try:
            value_receipt(db,d,org,loc,doc)
            assert False,"supposed to deny"
        except AutoStockDenied:
            pass
        assert db.execute("SELECT count(*) FROM auto_stock_receipt_valuation WHERE document_id=%s",
                          (doc,)).fetchone()==(0,)
    owner=token(http,d,d["other"])
    assert set_rule(http,policies,owner,role="MANAGER",loc=loc,
                    user=d["manager"],document="SUPPLIER_ORDER",
                    cap="PRICE_PURCHASE",allowed=True).status_code==201
    with psycopg.connect(DSN) as db:
        items,_=pending_receipt_reviews(db,d["tokens"][d["manager"]],org,loc,"value")
        assert any(x["documentId"]==str(doc) for x in items)
        assert value_receipt(db,d,org,loc,doc)


def url(org):
    return policy_url(org)


def test_tariff_and_complex_catalog_price_policy_blocks_alternate_urls(monkeypatch):
    d,org,loc,order,service,foreign,http=tariff_setup(monkeypatch)
    with psycopg.connect(DSN) as db:
        admin(db,org,d["other"])
    policies=policy_url(org)
    tariffs=tariff_url(org,loc)
    manager=token(http,d,d["intake"])
    assert http.get(tariffs).status_code==200
    owner=token(http,d,d["other"])
    assert set_rule(http,policies,owner,loc=loc,user=d["intake"],
                    document="ESTIMATE",cap="PRICE_SALES").status_code==201
    manager=token(http,d,d["intake"])
    assert http.get(tariffs).status_code==403
    assert http.post(tariffs,headers=manager,
                     json=proposal()).status_code==403
    owner=token(http,d,d["other"])
    assert set_rule(http,policies,owner,loc=loc,user=d["intake"],
                    document="ESTIMATE",cap="PRICE_SALES",allowed=True).status_code==201
    assert set_rule(http,policies,owner,loc=loc,user=d["intake"],
                    document="ESTIMATE",cap="DISCOUNT").status_code==201
    manager=token(http,d,d["intake"])
    assert http.post(tariffs,headers=manager,
                     json=proposal(discount=1000)).status_code==403
    created=http.post(tariffs,headers=manager,json=proposal(discount=0))
    assert created.status_code==201,created.text
    owner=token(http,d,d["other"])
    assert set_rule(http,policies,owner,loc=loc,user=d["intake"],
                    document="ESTIMATE",cap="READ",allowed=True,
                    not_before="2099-01-01T00:00:00Z").status_code==201
    manager=token(http,d,d["intake"])
    assert http.get(tariffs).status_code==403

    from test_pg_auto_work_complexes_http import (
        setup as complex_setup, paths as complex_paths,
        complex_body,
    )
    dd,oo,ll,wo,base,extra,part,consumable,web=complex_setup(monkeypatch)
    with psycopg.connect(DSN) as db:
        admin(db,oo,dd["other"])
    catalog,selected=complex_paths(oo,ll,wo)
    owner=token(web,dd,dd["other"])
    assert set_rule(web,policy_url(oo),owner,loc=ll,user=dd["intake"],
                    document="ESTIMATE",cap="PRICE_SALES").status_code==201
    manager=token(web,dd,dd["intake"])
    assert web.get(catalog).status_code==403
    assert web.post(catalog,headers=manager,
                    json=complex_body(extra,part,consumable)).status_code==403
    owner=token(web,dd,dd["other"])
    assert set_rule(web,policy_url(oo),owner,loc=ll,user=dd["intake"],
                    document="ESTIMATE",cap="PRICE_SALES",allowed=True).status_code==201
    manager=token(web,dd,dd["intake"])
    created=web.post(catalog,headers=manager,
                     json=complex_body(extra,part,consumable))
    assert created.status_code==201,created.text
    assert created.json()["complex"]["materials"][0]["unitPriceMinor"]>=0
    owner=token(web,dd,dd["other"])
    assert set_rule(web,policy_url(oo),owner,loc=ll,user=dd["intake"],
                    document="ESTIMATE",cap="CREATE").status_code==201
    manager=token(web,dd,dd["intake"])
    assert web.post(selected,headers=manager,json={
        "requestKey":str(uuid4()),
        "complexId":created.json()["complex"]["id"],
    }).status_code==403


def test_finance_history_cutoff_blocks_unfiltered_branch_aggregates(monkeypatch):
    """Never expose pre-cutoff accounts/treasury ledger through aggregated routes."""
    d,org,loc,order,service,http,base,_=treasury_prepare(monkeypatch)
    with psycopg.connect(DSN) as db:
        admin(db,org,d["other"])
    manager=token(http,d,d["intake"])
    assert treasury_create(http,base,manager).status_code==201
    day={"fromDay":"2030-01-01","toDay":"2030-01-01"}
    report=f"/api/v1/dev/auto/organizations/{org}/locations/{loc}/demo-finance-report"
    assert http.get(base+"/accounts").status_code==200
    assert http.get(report,params=day).status_code==200

    owner=token(http,d,d["other"])
    policy=policy_url(org)
    assert set_rule(http,policy,owner,role="MANAGER",loc=loc,
                    user=d["intake"],document="FINANCE",cap="READ",
                    allowed=True,not_before="2099-01-01T00:00:00Z").status_code==201
    manager=token(http,d,d["intake"])
    assert http.get(base+"/accounts").status_code==403
    assert http.get(base+"/report",params=day).status_code==403
    assert http.get(report,params=day).status_code==403
    assert http.get(base+f"/orders/{order}").status_code==403
    assert http.get(f"/api/v1/dev/auto/organizations/{org}/locations/{loc}"
                    f"/orders/{order}/financial-balance").status_code==403

    owner=token(http,d,d["other"])
    assert set_rule(http,policy,owner,role="MANAGER",loc=loc,
                    user=d["intake"],document="FINANCE",cap="READ",
                    allowed=True,not_before=None).status_code==201
    manager=token(http,d,d["intake"])
    assert http.get(base+"/accounts").status_code==200
    assert http.get(base+"/report",params=day).status_code==200
    assert http.get(report,params=day).status_code==200


def test_dual_role_cashier_deny_cannot_be_bypassed_using_manager_finance_routes(monkeypatch):
    """Require both live roles' restrictive policies while retaining MANAGER."""
    d,org,loc,order,service,http,base,_=treasury_prepare(monkeypatch)
    with psycopg.connect(DSN) as db:
        admin(db,org,d["other"])
        grant(db,d["intake"],org,loc,"CASHIER")
    root=f"/api/v1/dev/auto/organizations/{org}/locations/{loc}"
    balance=root+f"/orders/{order}/financial-balance"
    history=root+f"/orders/{order}/financial-history"
    post=root+f"/orders/{order}/financial-events"
    report=root+"/demo-finance-report"
    day={"fromDay":"2030-01-01","toDay":"2030-01-01"}
    manager=token(http,d,d["intake"])
    assert http.get(balance).status_code==200
    assert http.get(history).status_code==200
    assert http.get(report,params=day).status_code==200
    owner=token(http,d,d["other"])
    policy=policy_url(org)
    assert set_rule(http,policy,owner,role="CASHIER",loc=loc,
                    user=d["intake"],document="FINANCE",
                    cap="PRICE_SALES").status_code==201
    manager=token(http,d,d["intake"])
    assert http.get(balance).status_code==403
    assert http.get(history).status_code==403
    assert http.get(report,params=day).status_code==403
    assert http.post(post,headers={**manager,"Idempotency-Key":str(uuid4())},
                     json={"action":"CHARGE","amountMinor":27,
                           "reason":"Синтетическая корректировка"}).status_code==403

    owner=token(http,d,d["other"])
    assert set_rule(http,policy,owner,role="CASHIER",loc=loc,
                    user=d["intake"],document="FINANCE",
                    cap="PRICE_SALES",allowed=True).status_code==201
    assert set_rule(http,policy,owner,role="CASHIER",loc=loc,
                    user=d["intake"],document="FINANCE",
                    cap="EDIT").status_code==201
    manager=token(http,d,d["intake"])
    assert http.get(balance).status_code==200
    assert http.post(post,headers={**manager,"Idempotency-Key":str(uuid4())},
                     json={"action":"CHARGE","amountMinor":27,
                           "reason":"Синтетическая корректировка"}).status_code==403
    with psycopg.connect(DSN) as db:
        assert db.execute("""SELECT COUNT(*) FROM auto_work_order_financial_event
          WHERE work_order_id=%s""",(order,)).fetchone()==(0,)


def test_crm_sim_money_aggregate_obeys_finance_price_deny(monkeypatch):
    """CRM booked cohorts are no backdoor to finance-sensitive SIM amounts."""
    from test_pg_auto_document_lifecycle_http import ready
    from datetime import datetime
    from zoneinfo import ZoneInfo

    d,org,loc,order,act,preview=ready(monkeypatch)
    with psycopg.connect(DSN) as db:
        admin(db,org,d["other"])
    http=TestClient(app,base_url="https://127.0.0.1")
    endpoint=(f"/api/v1/dev/auto/organizations/{org}/locations/{loc}"
              "/crm/outcomes")
    day=datetime.now(ZoneInfo("Europe/Berlin")).date().isoformat()
    period={"fromDay":day,"toDay":day}
    manager=token(http,d,d["intake"])
    assert http.get(endpoint,params=period).status_code==200
    owner=token(http,d,d["other"])
    policies=policy_url(org)
    assert set_rule(http,policies,owner,loc=loc,user=d["intake"],
                    document="FINANCE",cap="PRICE_SALES").status_code==201
    manager=token(http,d,d["intake"])
    assert http.get(endpoint,params=period).status_code==403
    assert http.get(endpoint+"/export",params=period).status_code==403
    owner=token(http,d,d["other"])
    assert set_rule(http,policies,owner,loc=loc,user=d["intake"],
                    document="FINANCE",cap="PRICE_SALES",allowed=True).status_code==201
    manager=token(http,d,d["intake"])
    assert http.get(endpoint,params=period).status_code==200


def test_cashier_finance_cutoff_concurrent_read_and_payment_no_lock_upgrade(monkeypatch):
    """Under a non-empty historical cutoff, two requests must serialize.

    Both reads and writes request WorkOrder UPDATE before any history
    WorkOrder SHARE. The loser never sees an unhandled DeadlockDetected.
    """
    from roboport_api.auto_treasury import read_order,record_event
    d,org,loc,order,service,http,base,_=treasury_prepare(monkeypatch)
    with psycopg.connect(DSN) as db:
        admin(db,org,d["other"])
    manager=token(http,d,d["intake"])
    opened=treasury_create(http,base,manager)
    assert opened.status_code==201,opened.text
    account=UUID(opened.json()["accountId"])
    owner=token(http,d,d["other"])
    assert set_rule(http,policy_url(org),owner,role="CASHIER",loc=loc,
                    user=d["manager"],document="FINANCE",cap="READ",
                    allowed=True,not_before="2020-01-01T00:00:00Z").status_code==201

    start=Barrier(2)
    def read(_):
        with psycopg.connect(DSN) as db:
            db.execute("SET lock_timeout='5000ms'")
            start.wait(timeout=15)
            result=read_order(db,d["tokens"][d["manager"]],org,loc,order)
            return result["realMoney"],result["syntheticOnly"],result["orderId"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(read,range(2)))==[
            (False,True,str(order)),(False,True,str(order))
        ]

    start=Barrier(2)
    def advance(amount):
        with psycopg.connect(DSN) as db:
            db.execute("SET lock_timeout='5000ms'")
            start.wait(timeout=15)
            result=record_event(db,d["tokens"][d["manager"]],org,loc,order,
                account_id=account,request_key=uuid4(),kind="ADVANCE",
                amount_minor=amount,reason="Проверка гонки кассира")
            return result["replayed"],result["syntheticOnly"]
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(advance,(100,200)))==[
            (False,True),(False,True)
        ]
    with psycopg.connect(DSN) as db:
        assert db.execute("""SELECT COUNT(*),SUM(amount_minor)
            FROM auto_treasury_event WHERE work_order_id=%s
              AND kind='ADVANCE'""",(order,)).fetchone()==(2,300)
