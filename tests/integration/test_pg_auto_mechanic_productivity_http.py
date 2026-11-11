"""PG17/HTTPS end-to-end: approved execution -> independent QC -> sealed close -> labor report."""
from datetime import datetime, timezone
from uuid import uuid4, UUID
from zoneinfo import ZoneInfo
from io import StringIO
import csv

import psycopg
import pytest
from fastapi.testclient import TestClient

from roboport_api.main import app
from roboport_api.auto_mechanic_productivity import _period, _csv_export
from roboport_api.auto_document_lifecycle import record_document_event
from test_pg_auto_document_lifecycle_http import ready
from test_pg_baseline import DSN


def report_path(org, loc):
    return (f"/api/v1/dev/auto/organizations/{org}/locations/{loc}"
            "/reports/mechanic-productivity")


def _close(monkeypatch):
    d, org, loc, order, act, preview = ready(monkeypatch)
    with psycopg.connect(DSN) as db:
        for action, actor in (
            ("POSTED", d["intake"]),
            ("CLIENT_ACK", d["client_a"]),
            ("CLOSED_TECHNICAL", d["intake"]),
        ):
            result = record_document_event(
                db, d["tokens"][actor], org, loc, order,
                request_key=uuid4(), action=action,
                preview_id=UUID(preview["id"]),
                preview_digest=preview["digest"],
                intake_act_id=UUID(act["id"]),
                intake_digest=act["sha256"],
            )
            assert result["status"] == {
                "POSTED": "POSTED",
                "CLIENT_ACK": "CLIENT_ACKNOWLEDGED",
                "CLOSED_TECHNICAL": "CLOSED_TECHNICAL",
            }[action]
    return d, org, loc, order


def test_verified_labor_report_export_revocation_and_cross_branch(monkeypatch):
    d, org, loc, order = _close(monkeypatch)
    http = TestClient(app, base_url="https://127.0.0.1")
    path = report_path(org, loc)
    day = datetime.now(ZoneInfo("Europe/Berlin")).date().isoformat()
    query = {"fromDay": day, "toDay": day}
    assert http.get(path, params=query).status_code == 403
    http.cookies.set("rp_session", d["tokens"][d["client_a"]])
    assert http.get(path, params=query).status_code == 403
    http.cookies.set("rp_session", d["tokens"][d["intake"]])
    res = http.get(path, params=query)
    assert res.status_code == 200, res.text
    assert res.headers["cache-control"] == "no-store"
    data = res.json()
    assert {key: data[key] for key in (
        "syntheticOnly", "realMoney", "payrollAvailable", "releaseAllowed"
    )} == {
        "syntheticOnly": True, "realMoney": False,
        "payrollAvailable": False, "releaseAllowed": False,
    }
    assert data["basis"] == "CLOSED_TECHNICAL_QC_PASSED_ACTIVE_MECHANIC_LABOR"
    assert data["timeZone"] == "Europe/Berlin"
    assert data["summary"] == {
        "mechanicCount": 1, "workOrderCount": 1,
        "serviceCount": 1, "actualMinutes": 45,
    }
    assert len(data["items"]) == 1
    person = data["items"][0]
    assert person["mechanicId"] == str(d["manager"])
    assert person["orders"][0]["workOrderId"] == str(order)
    assert person["orders"][0]["serviceCount"] == 1
    assert person["orders"][0]["actualMinutes"] == 45
    assert len(person["orders"][0]["serviceIds"]) == 1

    export = http.get(path + "/export", params=query)
    assert export.status_code == 200, export.text
    assert export.headers["content-type"].startswith("text/csv")
    assert export.headers["x-content-type-options"] == "nosniff"
    assert "attachment" in export.headers["content-disposition"]
    lines = list(csv.reader(StringIO(export.text.lstrip("\ufeff")), delimiter=";"))
    assert len(lines) == 2
    assert lines[1][:2] == [str(d["manager"]), str(order)]
    assert lines[1][2:4] == ["1", "45"]
    assert all("RUB" not in field for row in lines for field in row)

    assert http.get(report_path(org, uuid4()), params=query).status_code == 403
    assert http.get(path, params={"fromDay": "2026-02-31", "toDay": day}).status_code == 422
    assert http.get(path, params={"fromDay": "2026-01-01", "toDay": day}).status_code == 422
    assert http.get(path, params={"fromDay": "2026-01-01", "toDay": "2025-12-31"}).status_code == 422
    with psycopg.connect(DSN) as db:
        db.execute("""UPDATE auto_organization_role_grant
          SET revoked_at=now()
          WHERE organization_id=%s AND user_id=%s AND role_code='MECHANIC'
            AND revoked_at IS NULL""", (org, d["manager"]))
    empty = http.get(path, params=query)
    assert empty.status_code == 200, empty.text
    assert empty.json()["summary"]["actualMinutes"] == 0
    assert empty.json()["items"] == []
    with psycopg.connect(DSN) as db:
        db.execute("""UPDATE auto_organization_role_grant
          SET revoked_at=now()
          WHERE organization_id=%s AND user_id=%s AND role_code='MANAGER'
            AND revoked_at IS NULL""", (org, d["intake"]))
    assert http.get(path, params=query).status_code == 403
    assert http.get(path + "/export", params=query).status_code == 403


def test_local_days_dst_and_export_contract():
    berlin = ZoneInfo("Europe/Berlin")
    spring = _period("2026-03-29", "2026-03-29", berlin)
    autumn = _period("2026-10-25", "2026-10-25", berlin)
    assert (spring[1]-spring[0]).total_seconds() == 23 * 3600
    assert (autumn[1]-autumn[0]).total_seconds() == 25 * 3600
    with pytest.raises(Exception):
        _period("2026-10-25", "2026-10-24", berlin)
    with pytest.raises(Exception):
        _period("2026-10-25", "2027-02-01", berlin)
    assert _csv_export({"items": []}).startswith("\ufeffМеханик UUID")
