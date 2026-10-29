"""PG17 HTTP cost/VAT reporting is not stock movement or retail price."""
from uuid import uuid4
import psycopg
from fastapi.testclient import TestClient

from roboport_api.main import app
from test_pg_baseline import DSN
from test_pg_auto_vehicle_claim_http import _scenario
from test_pg_auto_stock_receipt_valuation import setup_receipt
from test_pg_auto_stock_ledger import balance
from test_pg_auto_stock_lot_allocation import allocated


def post(client,d,actor,url,payload,key=None,csrf=True):
    client.cookies.set("rp_session",d["tokens"][actor])
    headers={"Origin":"https://127.0.0.1","Idempotency-Key":str(key or uuid4())}
    if csrf:
        headers["X-CSRF-Token"]=d["csrf"][actor]
    return client.post(url,json=payload,headers=headers)


def test_valuation_http_scoped_authorization_and_immutable_quote(monkeypatch):
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTO_API","1")
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTH_API","1")
    monkeypatch.setenv("ROBOport_DATABASE_URL",DSN)
    d=_scenario()
    with psycopg.connect(DSN) as db:
        org,loc,item,lot,doc=setup_receipt(db,d,3)
    url=f"/api/v1/dev/auto/organizations/{org}/warehouse/receipts/valuation"
    payload={"locationId":str(loc),"documentId":str(doc),"currency":"RUB",
             "unitNetMinor":1250,"vatTotalMinor":600,
             "supplierPriceRevision":"QUOTE-REV-1",
             "supplierQuotedAt":"2026-10-28T12:00:00+00:00"}
    with TestClient(app,base_url="https://127.0.0.1") as client:
        assert post(client,d,d["manager"],url,payload,csrf=False).status_code==403
        assert post(client,d,d["intake"],url,payload).status_code==403
        assert post(client,d,d["manager"],url,{**payload,"currency":"rub"}).status_code==422
        assert post(client,d,d["manager"],url,{**payload,"unitNetMinor":True}).status_code==422
        assert post(client,d,d["manager"],url,{**payload,"supplierQuotedAt":"2026-10-28T12:00:00"}).status_code==422
        assert post(client,d,d["manager"],url,{**payload,"locationId":str(uuid4())}).status_code==403
        key=uuid4()
        response=post(client,d,d["manager"],url,payload,key)
        assert response.status_code==201,response.text
        assert response.headers["cache-control"]=="no-store"
        assert post(client,d,d["manager"],url,payload,key).json()==response.json()
        assert post(client,d,d["manager"],url,{**payload,"vatTotalMinor":6000},key).status_code==409
        with psycopg.connect(DSN) as db:
            assert db.execute("""SELECT purchase_net_total_minor,purchase_vat_total_minor,
                 purchase_gross_total_minor FROM auto_stock_receipt_valuation_report
                 WHERE valuation_id=%s""",
                 (response.json()["valuationId"],)).fetchone()==(3750,600,4350)
            assert balance(db,item)==(4,0) and allocated(db,lot)==3
            grant_id=db.execute("""SELECT id FROM auto_organization_role_grant
                 WHERE user_id=%s AND organization_id=%s AND location_id=%s
                   AND role_code='MANAGER' AND revoked_at IS NULL LIMIT 1""",
                 (d["manager"],org,loc)).fetchone()[0]
            db.execute("UPDATE auto_organization_role_grant SET revoked_at=now() WHERE id=%s",
                       (grant_id,))
        assert post(client,d,d["manager"],url,payload,key).status_code==403


def test_valuation_http_disabled(monkeypatch):
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTO_API","0")
    monkeypatch.setenv("ROBOport_DATABASE_URL",DSN)
    with TestClient(app,base_url="https://127.0.0.1") as client:
        assert client.post(
            f"/api/v1/dev/auto/organizations/{uuid4()}/warehouse/receipts/valuation",
            json={}).status_code==404
