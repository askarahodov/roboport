"""PG17 + HTTPS: full synthetic treasury cashbook, exact source reconciliation and privacy."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from io import StringIO
from uuid import UUID, uuid4
import csv

import psycopg
import pytest

from roboport_api import auto_treasury_cashbook as module
from roboport_api.auto_treasury import record_event
from test_pg_auto_treasury_http import (
    prepare, create, event, complete_qc, local_day, session,
)
from test_pg_baseline import DSN


def _path(base, account):
    return base + f"/accounts/{account}/cashbook"


def _query(db, loc):
    day = local_day(db, loc)
    before = (date.fromisoformat(day) - timedelta(days=1)).isoformat()
    after = (date.fromisoformat(day) + timedelta(days=1)).isoformat()
    return day, {"fromDay": before, "toDay": after}


def test_cashbook_real_events_closed_day_csv_scope_revocation_and_limits(monkeypatch):
    d, org, loc, order, service, http, base, manager = prepare(monkeypatch)
    opened = create(http, base, manager)
    assert opened.status_code == 201, opened.text
    account = UUID(opened.json()["accountId"])
    url = _path(base, account)
    with psycopg.connect(DSN) as db:
        day, params = _query(db, loc)
    http.cookies.clear()
    assert http.get(url, params=params).status_code == 403
    session(http, d, d["client_a"])
    assert http.get(url, params=params).status_code == 403
    assert http.get(url + "/export", params=params).status_code == 403
    cashier = session(http, d, d["manager"])
    assert event(http, base, cashier, order, account, "ADVANCE", 900).status_code == 201
    assert event(http, base, cashier, order, account,
                 "REFUND_ADVANCE", 200).status_code == 201
    complete_qc(d, org, loc, order, service)
    assert event(http, base, cashier, order, account,
                 "APPLY_ADVANCE", 500).status_code == 201
    assert event(http, base, cashier, order, account,
                 "PAYMENT", 1000).status_code == 201
    assert event(http, base, cashier, order, account,
                 "REFUND_PAYMENT", 200).status_code == 201
    # 900 + 1000 - 200 - 200 = 1500; APPLY_ADVANCE 500 is internal.
    before = http.get(url, params=params)
    assert before.status_code == 200, before.text
    assert before.headers["cache-control"] == "no-store"
    result = before.json()
    assert result["syntheticOnly"] is True and result["realMoney"] is False
    assert result["releaseAllowed"] is False
    assert result["basis"] == "IMMUTABLE_TREASURY_ACCOUNT_CASHBOOK_DAYS"
    assert result["accountId"] == str(account)
    assert result["summary"] == {
        "openingMinor": "0", "incomingMinor": "1900",
        "outgoingMinor": "400", "internalAppliedMinor": "500",
        "closingMinor": "1500", "eventCount": 5, "sealedDayCount": 0,
    }
    assert len(result["items"]) == 3
    middle = result["items"][1]
    assert middle["localDay"] == day
    assert (middle["openingMinor"], middle["incomingMinor"],
            middle["outgoingMinor"], middle["internalAppliedMinor"],
            middle["closingMinor"]) == ("0", "1900", "400", "500", "1500")
    assert middle["closeId"] is None and len(middle["events"]) == 5
    assert sorted(x["cashFlow"] for x in middle["events"]) == [
        "IN", "IN", "INTERNAL", "OUT", "OUT"
    ]
    assert result["items"][0]["events"] == []
    assert result["items"][2]["openingMinor"] == "1500"
    assert str(order) not in before.text
    assert str(d["client_a"]) not in before.text
    assert "Тестовый акт оплаты" not in before.text
    raw = http.get(url + "/export", params=params)
    assert raw.status_code == 200
    assert raw.headers["x-content-type-options"] == "nosniff"
    assert raw.headers["cache-control"] == "no-store"
    assert raw.headers["content-type"].startswith("text/csv")
    lines = list(csv.reader(StringIO(raw.text.lstrip("\ufeff")), delimiter=";"))
    assert len(lines) == 8  # one header, five events and two empty days
    assert {row[3] for row in lines[1:]} == {
        "", "ADVANCE", "PAYMENT", "APPLY_ADVANCE",
        "REFUND_ADVANCE", "REFUND_PAYMENT",
    }
    assert all("Тестовый" not in field for row in lines for field in row)
    assert str(order) not in raw.text
    assert str(d["client_a"]) not in raw.text

    # A repeatable, real SQL limit branch: no truncated JSON or partial CSV.
    monkeypatch.setattr(module, "MAX_EVENTS", 4)
    assert http.get(url, params=params).status_code == 409
    failed_export = http.get(url + "/export", params=params)
    assert failed_export.status_code == 409
    assert "text/csv" not in failed_export.headers["content-type"]
    monkeypatch.setattr(module, "MAX_EVENTS", 1000)

    close = http.post(base+f"/accounts/{account}/close-day",
        headers=cashier, json={"requestKey": str(uuid4()), "businessDay": day})
    assert close.status_code == 201, close.text
    assert close.json()["openingMinor"] == 0
    assert close.json()["incomingMinor"] == 1900
    assert close.json()["outgoingMinor"] == 400
    assert close.json()["closingMinor"] == 1500
    sealed = http.get(url, params=params).json()
    assert sealed["summary"]["sealedDayCount"] == 1
    assert sealed["items"][1]["closeId"] == close.json()["closeId"]
    assert event(http, base, cashier, order, account,
                 "REFUND_PAYMENT", 1).status_code == 409

    assert http.get(_path(base, uuid4()), params=params).status_code == 403
    assert http.get(url.replace(str(org), str(uuid4())), params=params).status_code == 403
    assert http.get(url.replace(str(loc), str(uuid4())), params=params).status_code == 403
    assert http.get(base + "/accounts/not-uuid/cashbook", params=params).status_code == 422
    assert http.get(url, params={"fromDay":"2026-02-30","toDay":day}).status_code == 422
    assert http.get(url, params={"fromDay":"2026-01-01","toDay":day}).status_code == 422
    assert http.get(url, params={"fromDay":day,"toDay":"2025-01-01"}).status_code == 422

    # An active second MANAGER role can still read, but revoked CASHIER alone
    # must not keep a cashier-only session authorized to export.
    with psycopg.connect(DSN) as db:
        db.execute("""UPDATE auto_organization_role_grant
          SET revoked_at=now() WHERE organization_id=%s
          AND user_id=%s AND role_code='CASHIER' AND revoked_at IS NULL
        """, (org, d["manager"]))
    assert http.get(url, params=params).status_code == 403
    assert http.get(url + "/export", params=params).status_code == 403
    session(http, d, d["intake"])
    assert http.get(url, params=params).status_code == 200


def test_cashbook_account_share_lock_blocks_concurrent_append(monkeypatch):
    d, org, loc, order, service, http, base, manager = prepare(monkeypatch)
    account = UUID(create(http, base, manager).json()["accountId"])
    cashier = session(http, d, d["manager"])
    assert event(http, base, cashier, order, account,
                 "ADVANCE", 100).status_code == 201
    # Hold exactly the same account SHARE lock as cashbook() until the reader
    # completes. A concurrent authorized append requires account UPDATE.
    def attempt():
        with psycopg.connect(DSN) as connection:
            connection.execute("SET lock_timeout='250ms'")
            try:
                record_event(connection, d["tokens"][d["manager"]],
                    org,loc,order,account_id=account,request_key=uuid4(),
                    kind="ADVANCE",amount_minor=100,
                    reason="Concurrent synthetic advance")
                return "RECORDED"
            except psycopg.errors.LockNotAvailable:
                return "BLOCKED"
    with psycopg.connect(DSN) as reader:
        with reader.transaction():
            reader.execute("""SELECT id FROM auto_treasury_account
                WHERE id=%s FOR SHARE""",(account,))
            with ThreadPoolExecutor(max_workers=1) as pool:
                assert pool.submit(attempt).result(timeout=8) == "BLOCKED"
    with psycopg.connect(DSN) as db:
        assert db.execute("SELECT COUNT(*) FROM auto_treasury_event WHERE account_id=%s",
                          (account,)).fetchone()[0] == 1


def test_cashbook_csv_empty_day_and_prefix_safety():
    payload={"accountId":str(uuid4()), "items":[{
        "localDay":"2026-11-11","openingMinor":"0","incomingMinor":"0",
        "outgoingMinor":"0","internalAppliedMinor":"0","closingMinor":"0",
        "closeId":None,"events":[],
    }]}
    csv_data=module._csv(payload)
    rows=list(csv.reader(StringIO(csv_data.lstrip("\ufeff")),delimiter=";"))
    assert len(rows)==2
    assert rows[1][2:6]==["","","",""]
    assert rows[1][-1]=="SIM_ONLY_NOT_REAL_MONEY"
