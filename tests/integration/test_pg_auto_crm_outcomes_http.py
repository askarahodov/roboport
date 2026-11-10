"""PG17 + HTTPS regression: CRM lead → closed technical order → SIM treasury only."""
import csv
from datetime import datetime, timezone
from io import StringIO
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import psycopg
from fastapi.testclient import TestClient

from roboport_api.main import app
from roboport_api.auto_crm_leads import create_lead, transition_lead
from roboport_api.auto_crm_outcomes import _csv, _blank
from roboport_api.auto_document_lifecycle import record_document_event
from roboport_api.auto_treasury import create_account, record_event
from test_pg_auto_document_lifecycle_http import ready
from test_pg_auto_stock_service import grant
from test_pg_baseline import DSN


def url(org, loc):
    return (f"/api/v1/dev/auto/organizations/{org}"
            f"/locations/{loc}/crm/outcomes")


def _client():
    return TestClient(app, base_url="https://127.0.0.1")


def _day():
    return datetime.now(ZoneInfo("Europe/Berlin")).date().isoformat()


def _posted(db, d, org, loc, order, act, preview, action, actor):
    return record_document_event(
        db, d["tokens"][actor], org, loc, order, request_key=uuid4(),
        action=action, preview_id=UUID(preview["id"]),
        preview_digest=preview["digest"], intake_act_id=UUID(act["id"]),
        intake_digest=act["sha256"],
    )


def test_crm_technical_conversion_counts_only_closed_and_simulated_settlement(monkeypatch):
    d, org, loc, order, act, preview = ready(monkeypatch)
    http = _client()
    endpoint = url(org, loc)
    query = {"fromDay": _day(), "toDay": _day()}
    assert http.get(endpoint, params=query).status_code == 403
    http.cookies.set("rp_session", d["tokens"][d["other"]])
    assert http.get(endpoint, params=query).status_code == 403

    with psycopg.connect(DSN) as db:
        appointment = db.execute(
            "SELECT appointment_id FROM auto_work_order WHERE id=%s",
            (order,),
        ).fetchone()[0]
        lead = create_lead(db, d["tokens"][d["intake"]],
                           org, loc, d["link"], "WEB", uuid4())
        ident = UUID(lead["leadId"])
        transition_lead(db, d["tokens"][d["intake"]], org, loc,
                        ident, uuid4(), "QUALIFIED", None, None)
        transition_lead(db, d["tokens"][d["intake"]], org, loc,
                        ident, uuid4(), "BOOKED", None, appointment)

        grant(db, d["intake"], org, loc, "CASHIER")
        account = create_account(db, d["tokens"][d["intake"]],
                                 org, loc, request_key=uuid4(), code="CRM_SIM",
                                 label="Синтетическая касса", kind="CASH_SIM")
        paid = record_event(db, d["tokens"][d["intake"]], org, loc, order,
                            account_id=UUID(account["accountId"]),
                            request_key=uuid4(), kind="PAYMENT",
                            amount_minor=2500, reason="Только тестовый платёж")
        assert paid["syntheticOnly"] is True
        _posted(db, d, org, loc, order, act, preview, "POSTED", d["intake"])

    http.cookies.set("rp_session", d["tokens"][d["intake"]])
    not_closed = http.get(endpoint, params=query)
    assert not_closed.status_code == 200, not_closed.text
    assert not_closed.json()["summary"] == {
        "sourceCode": "ALL", "bookedLeads": 1, "closedTechnical": 0,
        "simSettledMinor": "0", "simRefundMinor": "0",
    }
    with psycopg.connect(DSN) as db:
        _posted(db, d, org, loc, order, act, preview, "CLIENT_ACK", d["client_a"])
        _posted(db, d, org, loc, order, act, preview,
                "CLOSED_TECHNICAL", d["intake"])
    result = http.get(endpoint, params=query)
    assert result.status_code == 200, result.text
    body = result.json()
    assert body["syntheticOnly"] is True
    assert body["realMoney"] is False
    assert body["revenueAvailable"] is False
    assert body["releaseAllowed"] is False
    assert body["basis"] == "LEAD_COHORT_CLOSED_TECHNICAL_SIM_TREASURY"
    assert body["summary"] == {
        "sourceCode": "ALL", "bookedLeads": 1, "closedTechnical": 1,
        "simSettledMinor": "2500", "simRefundMinor": "0",
    }
    assert body["items"][0]["simSettledMinor"] == "2500"
    assert body["items"][1] == _blank("PHONE")
    for secret in (str(order), str(ident), str(appointment), str(d["ca"]),
                   str(d["link"]), str(d["client_a"])):
        assert secret not in result.text

    export = http.get(endpoint + "/export", params=query)
    assert export.status_code == 200, export.text
    assert export.headers["cache-control"] == "no-store"
    assert export.headers["x-content-type-options"] == "nosniff"
    rows = list(csv.reader(StringIO(export.text.lstrip("\ufeff")), delimiter=";"))
    assert rows[1] == ["Все источники", "1", "1", "2500", "0"]
    assert rows[2] == ["Сайт", "1", "1", "2500", "0"]
    assert len(rows) == 5
    assert all(secret not in export.text for secret in
               (str(order), str(ident), str(d["link"])))

    # A customer revocation must also remove already completed SIM
    # balances from the live manager cohort (no historical PII leakage).
    with psycopg.connect(DSN) as db:
        db.execute(
            "UPDATE auto_vehicle_customer_link SET revoked_at=now() WHERE id=%s",
            (d["link"],),
        )
    after = http.get(endpoint, params=query)
    assert after.status_code == 200
    assert after.json()["summary"] == _blank("ALL")


def test_crm_outcomes_forbidden_invalid_and_restricted_scope(monkeypatch):
    d, org, loc, order, act, preview = ready(monkeypatch)
    endpoint = url(org, loc)
    query = {"fromDay": _day(), "toDay": _day()}
    http = _client()
    http.cookies.set("rp_session", d["tokens"][d["intake"]])
    for first, last in [
        ("not-date", "2030-01-01"),
        ("2030-02-29", "2030-03-01"),
        ("2030-01-01", "2030-02-01"),
        ("2030-01-03", "2030-01-01"),
    ]:
        assert http.get(endpoint, params={
            "fromDay": first, "toDay": last,
        }).status_code == 422
    assert http.get(endpoint).status_code == 422
    assert http.get(url("invalid", loc), params=query).status_code == 422
    assert http.get(url(uuid4(), loc), params=query).status_code == 403
    assert http.get(url(org, uuid4()), params=query).status_code == 403
    assert http.get(endpoint + "/export", params=query).status_code == 200

    with psycopg.connect(DSN) as db:
        db.execute("""
            UPDATE auto_organization_role_grant SET revoked_at=now()
            WHERE organization_id=%s AND location_id=%s AND user_id=%s
              AND role_code='MANAGER'
        """, (org, loc, d["intake"]))
    assert http.get(endpoint, params=query).status_code == 403
    assert http.get(endpoint + "/export", params=query).status_code == 403


def test_crm_outcomes_empty_export_remains_numeric_and_csv_safe():
    report = {"summary": _blank("ALL"),
              "items": [_blank(s) for s in ("WEB", "PHONE", "WALK_IN")]}
    csv_text = _csv(report)
    rows = list(csv.reader(StringIO(csv_text.lstrip("\ufeff")), delimiter=";"))
    assert len(rows) == 5
    assert rows[1] == ["Все источники", "0", "0", "0", "0"]
    assert all(not cell.startswith(("=", "+", "-", "@")) for row in rows
               for cell in row)
