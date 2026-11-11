"""Full dual-role negative regression: no MANAGER/WAREHOUSE ACL bypass.

Real PostgreSQL grants, append-only ADMIN policies, HTTPS sessions, CSRF,
replay and inventory/documents. The permission service is never mocked.
"""
from uuid import uuid4

import psycopg
from fastapi.testclient import TestClient

from roboport_api.main import app
from test_pg_baseline import DSN
from test_pg_auto_document_access_http import admin, set_rule, url as policy_url
from test_pg_auto_work_order_estimate_http import token
from test_pg_auto_stock_service import grant
from test_pg_auto_vehicle_claim_http import _scenario
from test_pg_auto_stock_receipt_valuation import setup_receipt
from test_pg_auto_receipt_valuation_http import post as receipt_post
from test_pg_auto_supplier_purchase_http import fixture as purchase_fixture, post as supplier_post
from test_pg_auto_sku_catalog_http import fixture as catalog_fixture, post as catalog_post, listed as catalog_listed, url as catalog_url, row as catalog_row


def _api(monkeypatch):
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTO_API", "1")
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTH_API", "1")
    monkeypatch.setenv("ROBOport_DATABASE_URL", DSN)
    return TestClient(app, base_url="https://127.0.0.1")


def _rule(http, policy, d, org, loc, role, capability, allowed, *, document="SUPPLIER_ORDER"):
    owner = token(http, d, d["other"])
    result = set_rule(
        http, policy, owner, role=role, loc=loc, user=None, document=document,
        cap=capability, allowed=allowed,
    )
    assert result.status_code == 201, result.text


def test_receipt_pending_and_valuation_respect_second_warehouse_role(monkeypatch):
    http = _api(monkeypatch)
    d = _scenario()
    with psycopg.connect(DSN) as db:
        org, loc, sku, lot, document = setup_receipt(db, d, accept=True)
        admin(db, org, d["other"])
        grant(db, d["manager"], org, loc, "WAREHOUSE")
    policy = policy_url(org)
    pending = f"/api/v1/dev/auto/organizations/{org}/warehouse/receipts/pending"
    valuation = f"/api/v1/dev/auto/organizations/{org}/warehouse/receipts/valuation"
    query = {"locationId": str(loc), "stage": "value"}
    payload = {"locationId": str(loc), "documentId": str(document),
               "currency": "RUB", "unitNetMinor": 1250, "vatTotalMinor": 600,
               "supplierPriceRevision": "OFFER-REVISION-1",
               "supplierQuotedAt": "2026-10-28T12:00:00+00:00"}
    manager = d["manager"]
    http.cookies.set("rp_session", d["tokens"][manager])
    assert http.get(pending, params=query).status_code == 200
    _rule(http, policy, d, org, loc, "WAREHOUSE", "PRICE_PURCHASE", False)
    http.cookies.set("rp_session", d["tokens"][manager])
    assert http.get(pending, params=query).status_code == 403
    assert receipt_post(http, d, manager, valuation, payload).status_code == 403
    with psycopg.connect(DSN) as db:
        assert db.execute("SELECT count(*) FROM auto_stock_receipt_valuation "
                          "WHERE document_id=%s", (document,)).fetchone() == (0,)
    # A personal or independent MANAGER ALLOW cannot bypass WAREHOUSE deny.
    _rule(http, policy, d, org, loc, "MANAGER", "PRICE_PURCHASE", True)
    http.cookies.set("rp_session", d["tokens"][manager])
    assert http.get(pending, params=query).status_code == 403
    _rule(http, policy, d, org, loc, "WAREHOUSE", "PRICE_PURCHASE", True)
    http.cookies.set("rp_session", d["tokens"][manager])
    assert http.get(pending, params=query).status_code == 200
    _rule(http, policy, d, org, loc, "WAREHOUSE", "READ", False)
    http.cookies.set("rp_session", d["tokens"][manager])
    assert http.get(pending, params=query).status_code == 403
    _rule(http, policy, d, org, loc, "WAREHOUSE", "READ", True)
    http.cookies.set("rp_session", d["tokens"][manager])
    assert http.get(pending, params=query).status_code == 200
    created = receipt_post(http, d, manager, valuation, payload)
    assert created.status_code == 201, created.text
    # Strict live policy also covers same-key replay after a price revocation.
    _rule(http, policy, d, org, loc, "WAREHOUSE", "PRICE_PURCHASE", False)
    assert receipt_post(http, d, manager, valuation, payload,
                        key=uuid4()).status_code == 403
    with psycopg.connect(DSN) as db:
        assert db.execute("SELECT count(*) FROM auto_stock_receipt_valuation "
                          "WHERE document_id=%s", (document,)).fetchone() == (1,)


def test_supplier_create_receive_cancel_intersects_manager_policy(monkeypatch):
    d, org, loc, sku, lot, order, offers, _, _, base, body = purchase_fixture(monkeypatch)
    with psycopg.connect(DSN) as db:
        grant(db, d["intake"], org, loc, "MANAGER")
        admin(db, org, d["other"])
    policy = policy_url(org)
    http = TestClient(app, base_url="https://127.0.0.1")
    warehouse = d["intake"]
    _rule(http, policy, d, org, loc, "MANAGER", "PRICE_PURCHASE", False)
    key = uuid4()
    assert supplier_post(http, d, warehouse, base, body, key).status_code == 403
    with psycopg.connect(DSN) as db:
        assert db.execute("SELECT count(*) FROM auto_supplier_purchase_order "
                          "WHERE work_order_id=%s", (order,)).fetchone() == (0,)
    _rule(http, policy, d, org, loc, "MANAGER", "PRICE_PURCHASE", True)
    _rule(http, policy, d, org, loc, "MANAGER", "CREATE", False)
    assert supplier_post(http, d, warehouse, base, body, key).status_code == 403
    _rule(http, policy, d, org, loc, "MANAGER", "CREATE", True)
    placed = supplier_post(http, d, warehouse, base, body, key)
    assert placed.status_code == 201, placed.text
    order_id = placed.json()["orderId"]
    assert supplier_post(http, d, warehouse, base, body, key).status_code == 201
    receipt = base + "/" + order_id + "/receipts"
    delivery = {"locationId": str(loc), "quantity": 1,
                "supplierReference": "DUAL-ROLE-DELIVERY-1"}
    _rule(http, policy, d, org, loc, "MANAGER", "EDIT", False)
    assert supplier_post(http, d, warehouse, receipt, delivery).status_code == 403
    with psycopg.connect(DSN) as db:
        assert db.execute("SELECT count(*) FROM auto_supplier_purchase_receipt "
                          "WHERE purchase_id=%s", (order_id,)).fetchone() == (0,)
    _rule(http, policy, d, org, loc, "MANAGER", "EDIT", True)
    delivered = supplier_post(http, d, warehouse, receipt, delivery)
    assert delivered.status_code == 201, delivered.text
    _rule(http, policy, d, org, loc, "MANAGER", "CLOSE", False)
    cancel = base + "/" + order_id + "/cancel"
    payload = {"locationId": str(loc)}
    assert supplier_post(http, d, warehouse, cancel, payload).status_code == 403
    _rule(http, policy, d, org, loc, "MANAGER", "CLOSE", True)
    assert supplier_post(http, d, warehouse, cancel, payload).status_code == 201
    with psycopg.connect(DSN) as db:
        assert db.execute("SELECT count(*) FROM auto_supplier_purchase_cancel "
                          "WHERE purchase_id=%s", (order_id,)).fetchone() == (1,)
    # Revoke active WAREHOUSE grant: remaining MANAGER cannot create orders.
    with psycopg.connect(DSN) as db:
        db.execute("""UPDATE auto_organization_role_grant SET revoked_at=now()
          WHERE user_id=%s AND organization_id=%s AND role_code='WAREHOUSE'
          AND revoked_at IS NULL""", (warehouse, org))
    assert supplier_post(http, d, warehouse, base, body).status_code == 403


def test_sku_catalog_purchase_and_sales_intersections_and_warehouse_only(monkeypatch):
    d, org, loc, _, _, _, _ = catalog_fixture(monkeypatch)
    with psycopg.connect(DSN) as db:
        admin(db, org, d["other"])
    http = TestClient(app, base_url="https://127.0.0.1")
    policy = policy_url(org)
    endpoint = catalog_url(org) + "/skus"
    payload = {"locationId": str(loc), **{
        k: v for k,v in catalog_row("SEC-ACL-498-A").items()
        if k != "analogArticle"
    }}
    _rule(http, policy, d, org, loc, "MANAGER", "PRICE_PURCHASE", False)
    assert catalog_post(http, d, d["intake"], endpoint, payload).status_code == 403
    _rule(http, policy, d, org, loc, "MANAGER", "PRICE_PURCHASE", True)
    # WAREHOUSE denial of sales-price capability must block SKU mutation
    # even if a MANAGER capability has been allowed separately.
    _rule(http, policy, d, org, loc, "WAREHOUSE", "PRICE_SALES", False,
          document="ESTIMATE")
    assert catalog_post(http, d, d["intake"], endpoint, payload).status_code == 403
    _rule(http, policy, d, org, loc, "MANAGER", "PRICE_SALES", True,
          document="ESTIMATE")
    assert catalog_post(http, d, d["intake"], endpoint, payload).status_code == 403
    view = catalog_listed(http, d, org, loc)
    assert view.status_code == 200, view.text
    _rule(http, policy, d, org, loc, "WAREHOUSE", "PRICE_SALES", True,
          document="ESTIMATE")
    made = catalog_post(http, d, d["intake"], endpoint, payload)
    assert made.status_code == 201, made.text
    assert catalog_listed(http, d, org, loc).status_code == 200
    _rule(http, policy, d, org, loc, "MANAGER", "EDIT", False,
          document="SUPPLIER_ORDER")
    update = {"locationId": str(loc), "entries": [{
        "skuId": made.json()["items"][0]["skuId"], "expectedVersion": 1,
        "purchaseMinor": 1200, "saleMinor": 2500
    }]}
    assert catalog_post(http, d, d["intake"],
                        catalog_url(org) + "/prices", update).status_code == 403
    _rule(http, policy, d, org, loc, "MANAGER", "EDIT", True)
    assert catalog_post(http, d, d["intake"],
                        catalog_url(org) + "/prices", update).status_code == 201

    # A warehouse-only employee must not inherit sales-price visibility.
    with psycopg.connect(DSN) as db:
        db.execute("""UPDATE auto_organization_role_grant SET revoked_at=now()
          WHERE user_id=%s AND organization_id=%s AND role_code='MANAGER'
            AND revoked_at IS NULL""", (d["intake"], org))
    view = catalog_listed(http, d, org, loc)
    assert view.status_code == 200, view.text
    items = view.json()["items"]
    assert items
    assert all("saleMinor" not in i or i["saleMinor"] is None for i in items)
    assert catalog_post(http, d, d["intake"], endpoint, payload).status_code == 403
