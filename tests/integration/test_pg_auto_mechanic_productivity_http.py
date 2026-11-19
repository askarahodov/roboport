"""PG17/HTTPS end-to-end: approved execution -> independent QC -> sealed close -> labor report."""
from datetime import datetime, time, timezone
from uuid import uuid4, UUID
from zoneinfo import ZoneInfo
from io import StringIO
import csv

import psycopg
import pytest
from fastapi.testclient import TestClient

from roboport_api.main import app
from roboport_api.auto_mechanic_productivity import _period, _csv_export
from roboport_api import auto_mechanic_productivity as productivity
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
    with psycopg.connect(DSN) as db:
        time_zone = db.execute(
            "SELECT timezone_name FROM auto_service_location "
            "WHERE id=%s AND organization_id=%s", (loc, org),
        ).fetchone()[0]
    day = datetime.now(ZoneInfo(time_zone)).date().isoformat()
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
    assert data["basis"] == "CLOSED_TECHNICAL_LOCAL_DAY_QC_PASS_LABOR_SOURCES"
    assert data["periodBasis"] == "CLOSED_TECHNICAL_LOCAL_DAY"
    assert data["timeZone"] == time_zone
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
    evidence=person["orders"][0]
    with psycopg.connect(DSN) as db:
        source=db.execute("""
          SELECT l.id,finish.id,qc.id,closed.id,closed.created_at
          FROM auto_work_order_labor_entry l
          JOIN auto_work_order_execution e ON e.id=l.execution_id
          JOIN auto_work_order_execution_finish finish ON finish.execution_id=e.id
          JOIN auto_order_demo_document_event closed
            ON closed.work_order_id=e.work_order_id AND closed.action='CLOSED_TECHNICAL'
          JOIN auto_work_order_qc_event qc ON qc.work_order_id=e.work_order_id
            AND qc.result='PASS'
          WHERE e.work_order_id=%s
        """,(order,)).fetchone()
    labor,finish,qc,closed,closed_at=source
    assert evidence["laborEntries"]==[{
        "laborEntryId":str(labor),"serviceId":evidence["serviceIds"][0],
        "actualMinutes":45,
    }]
    assert evidence["finishEventId"]==str(finish)
    assert evidence["qcEventId"]==str(qc)
    assert evidence["closedEventId"]==str(closed)
    assert evidence["closedDay"]==closed_at.astimezone(ZoneInfo(time_zone)).date().isoformat()

    export = http.get(path + "/export", params=query)
    assert export.status_code == 200, export.text
    assert export.headers["content-type"].startswith("text/csv")
    assert export.headers["x-content-type-options"] == "nosniff"
    assert "attachment" in export.headers["content-disposition"]
    lines = list(csv.reader(StringIO(export.text.lstrip("\ufeff")), delimiter=";"))
    assert len(lines) == 3 and all(len(row)==12 for row in lines)
    assert lines[0][7:11]==["Запись труда UUID","Услуга UUID","Услуг","Факт, минут"]
    assert [row[3] for row in lines[1:]]==["ORDER","LABOR"]
    assert all(row[:2] == [str(d["manager"]), str(order)] for row in lines[1:])
    assert lines[1][9:11]==["1","45"]
    assert lines[2][7]==str(labor) and lines[2][10]=="45"
    assert lines[1][4:7]==[str(closed),str(finish),str(qc)]
    assert all("RUB" not in field for row in lines for field in row)

    # Exercise the real PostgreSQL capacity branch deterministically:
    # with one qualifying labor entry, a temporary zero cap must produce
    # a full 409 for both JSON and CSV, not a silently truncated report.
    monkeypatch.setattr(productivity, "MAX_LABOR_ENTRIES", 0)
    assert http.get(path, params=query).status_code == 409
    assert http.get(path + "/export", params=query).status_code == 409
    monkeypatch.setattr(productivity, "MAX_LABOR_ENTRIES", 500)

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


@pytest.mark.parametrize("failure,code", [
    (productivity.ProductivityLimit, "REPORT_LIMIT"),
    (psycopg.errors.SerializationFailure, "REPORT_CONFLICT"),
    (psycopg.errors.DeadlockDetected, "REPORT_CONFLICT"),
])
def test_report_failure_codes_are_distinct_for_json_and_csv(monkeypatch, failure, code):
    """Capacity and concurrent revocation must not share a misleading error."""
    monkeypatch.setattr(productivity, "_request_dsn", lambda request: "unused")

    def fail_connect(dsn):
        raise failure("synthetic test failure")

    monkeypatch.setattr(productivity.psycopg, "connect", fail_connect)
    client = TestClient(app, base_url="https://127.0.0.1")
    path = report_path(uuid4(), uuid4())
    for url in (path, path + "/export"):
        result = client.get(url, params={"fromDay": "2026-11-18", "toDay": "2026-11-18"})
        assert result.status_code == 409
        assert code in result.text
        assert result.headers.get("content-type", "").startswith("application/json")
        assert "Механик UUID" not in result.text


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


def test_report_only_after_technical_close_and_with_source_order(monkeypatch):
    """Finish and QC in isolation are not counted in any day of the report."""
    d,org,loc,order,act,preview=ready(monkeypatch)
    http=TestClient(app,base_url="https://127.0.0.1")
    http.cookies.set("rp_session",d["tokens"][d["intake"]])
    with psycopg.connect(DSN) as db:
        zone=ZoneInfo(db.execute("SELECT timezone_name FROM auto_service_location WHERE id=%s",
                                 (loc,)).fetchone()[0])
    period={"fromDay":datetime.now(zone).date().isoformat(),
            "toDay":datetime.now(zone).date().isoformat()}
    path=report_path(org,loc)
    def count():
        result=http.get(path,params=period)
        assert result.status_code==200,result.text
        return result.json()["summary"]["serviceCount"]
    assert count()==0
    with psycopg.connect(DSN) as db:
        for action,actor in (("POSTED",d["intake"]),("CLIENT_ACK",d["client_a"])):
            record_document_event(db,d["tokens"][actor],org,loc,order,
               request_key=uuid4(),action=action,
               preview_id=UUID(preview["id"]),preview_digest=preview["digest"],
               intake_act_id=UUID(act["id"]),intake_digest=act["sha256"])
    assert count()==0
    with psycopg.connect(DSN) as db:
        record_document_event(db,d["tokens"][d["intake"]],org,loc,order,
            request_key=uuid4(),action="CLOSED_TECHNICAL",
            preview_id=UUID(preview["id"]),preview_digest=preview["digest"],
            intake_act_id=UUID(act["id"]),intake_digest=act["sha256"])
    assert count()==1
    assert http.get(path+"/export",params=period).status_code==200


def test_manager_shift_labor_comparison_real_pg_sources_revoke_and_csv(monkeypatch):
    """A complete synthetic user journey: closed QC labor, idle shift, audit CSV."""
    d,org,loc,order=_close(monkeypatch)
    http=TestClient(app,base_url="https://127.0.0.1")
    base=report_path(org,loc)+"/shift-comparison"
    with psycopg.connect(DSN) as db:
        zone=ZoneInfo(db.execute(
            "SELECT timezone_name FROM auto_service_location WHERE id=%s", (loc,)
        ).fetchone()[0])
        day=datetime.now(zone).date()
        first=datetime.combine(day,time(10,0),tzinfo=zone)
        last=datetime.combine(day,time(12,0),tzinfo=zone)
        shift_work,shift_idle=uuid4(),uuid4()
        for shift,actor,lo,hi in (
            (shift_work,d["manager"],first,last),
            (shift_idle,d["other"],first,last),
        ):
            db.execute("""INSERT INTO auto_staff_shift
               (id,organization_id,location_id,user_id,starts_at,ends_at)
               VALUES(%s,%s,%s,%s,%s,%s)""",(shift,org,loc,actor,lo,hi))
    query={"fromDay":day.isoformat(),"toDay":day.isoformat()}
    assert http.get(base,params=query).status_code==403
    http.cookies.set("rp_session",d["tokens"][d["client_a"]])
    assert http.get(base,params=query).status_code==403
    http.cookies.set("rp_session",d["tokens"][d["intake"]])
    res=http.get(base,params=query)
    assert res.status_code==200,res.text
    assert res.headers["cache-control"]=="no-store"
    data=res.json()
    assert data["summary"]["actualMinutes"]==45
    comp=data["shiftComparison"]
    assert comp["basis"]=="ACTIVE_SHIFTS_VS_CLOSED_TECHNICAL_LABOR"
    assert comp["isAttendance"] is False and comp["isPayroll"] is False
    assert comp["summary"]=={
        "mechanicCount":2,"shiftedMechanicCount":2,"shiftCount":2,
        "scheduledSeconds":14400,"recordedMinutes":45,
    }
    people={x["mechanicId"]:x for x in comp["items"]}
    worker=people[str(d["manager"])]
    assert worker["status"]=="COMPARABLE"
    assert worker["recordedMinutes"]==45
    assert worker["scheduledSeconds"]==7200
    assert worker["recordedToScheduledBasisPoints"]==3750
    assert worker["shifts"][0]["shiftId"]==str(shift_work)
    idle=people[str(d["other"])]
    assert idle["status"]=="NO_CLOSED_LABOR"
    assert idle["recordedMinutes"]==0
    assert idle["recordedToScheduledBasisPoints"]==0
    assert idle["shifts"][0]["shiftId"]==str(shift_idle)
    csv_response=http.get(base+"/export",params=query)
    assert csv_response.status_code==200
    assert csv_response.headers["x-content-type-options"]=="nosniff"
    rows=list(csv.reader(StringIO(csv_response.text.lstrip("\ufeff")),delimiter=";"))
    assert len(rows)==5 and all(len(r)==10 for r in rows)
    assert [row[1] for row in rows[1:]].count("SHIFT")==2
    assert str(shift_work) in csv_response.text
    assert str(order) not in csv_response.text
    assert http.get(base.replace(str(loc),str(uuid4())),params=query).status_code==403
    assert http.get(base,params={"fromDay":"2026-02-31","toDay":day.isoformat()}).status_code==422
    monkeypatch.setattr(productivity,"MAX_SHIFT_ROWS",0)
    assert http.get(base,params=query).status_code==409
    assert http.get(base+"/export",params=query).status_code==409
    monkeypatch.setattr(productivity,"MAX_SHIFT_ROWS",500)
    with psycopg.connect(DSN) as db:
        db.execute("UPDATE auto_staff_shift SET canceled_at=now() WHERE id=%s",(shift_idle,))
    after=http.get(base,params=query).json()["shiftComparison"]
    assert after["summary"]["shiftedMechanicCount"]==1
    assert str(d["other"]) not in [x["mechanicId"] for x in after["items"]]
    with psycopg.connect(DSN) as db:
        db.execute("""UPDATE auto_organization_role_grant SET revoked_at=now()
            WHERE organization_id=%s AND user_id=%s AND role_code='MANAGER'
              AND revoked_at IS NULL""",(org,d["intake"]))
    assert http.get(base,params=query).status_code==403
    assert http.get(base+"/export",params=query).status_code==403


def test_postgres_berlin_shift_elapsed_hours_at_both_dst_boundaries(monkeypatch):
    """A real PG session in Europe/Berlin returns same-zone Python datetimes.

    March shift 01–04 = 2 elapsed hours; October shift 01–04 = 4.
    Wall-clock subtraction would incorrectly return 3 for both.
    """
    d,org,loc,order=_close(monkeypatch)
    zone=ZoneInfo("Europe/Berlin")
    cases=(("2026-03-29",7200),("2026-10-25",14400))
    with psycopg.connect(DSN) as db:
        db.execute("SET TIME ZONE 'Europe/Berlin'")
        for day,_ in cases:
            local=datetime.fromisoformat(day)
            start=datetime(local.year,local.month,local.day,1,0,tzinfo=zone)
            end=datetime(local.year,local.month,local.day,4,0,tzinfo=zone)
            db.execute("""INSERT INTO auto_staff_shift
               (id,organization_id,location_id,user_id,starts_at,ends_at)
               VALUES(%s,%s,%s,%s,%s,%s)""",
               (uuid4(),org,loc,d["manager"],start,end))
        for day,expected in cases:
            start,end=_period(day,day,zone)
            comparison=productivity._shift_comparison(db,org,loc,start,end,[])
            assert comparison["summary"]["shiftCount"]==1
            assert comparison["summary"]["scheduledSeconds"]==expected
            row=comparison["items"][0]
            assert row["scheduledSeconds"]==expected
            assert row["shifts"][0]["overlapSeconds"]==expected
            assert row["recordedToScheduledBasisPoints"]==0


def test_shift_comparison_daylight_saving_range_and_csv_contract():
    """Midnight DST days differ from fixed 24h; export never carries names."""
    berlin=ZoneInfo("Europe/Berlin")
    assert (_period("2026-03-29","2026-03-29",berlin)[1]-
            _period("2026-03-29","2026-03-29",berlin)[0]).total_seconds()==23*3600
    assert (_period("2026-10-25","2026-10-25",berlin)[1]-
            _period("2026-10-25","2026-10-25",berlin)[0]).total_seconds()==25*3600
    assert productivity._csv_shift_comparison({
        "shiftComparison":{"items":[]}
    }).startswith("\ufeffМеханик UUID;")
