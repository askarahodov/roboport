"""PostgreSQL17 + HTTPS HTTP: object-scoped INTAKE delegation и необратимый аудит."""
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import psycopg
import pytest

from roboport_api.auto_intake_delegation import AssignmentConflict, grant_intake
from test_pg_auto_vehicle_claim_http import _scenario, _act, _http
from test_pg_baseline import DSN


def _enable(monkeypatch):
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTO_API", "1")
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTH_API", "1")
    monkeypatch.setenv("ROBOport_DATABASE_URL", DSN)


def _eligible(db, user):
    db.execute("INSERT INTO user_role(id,user_id,role_code) VALUES(%s,%s,'INTAKE')",
               (uuid4(), user))


def test_manager_scoped_grant_revoke_no_self_escalation_or_spoof(monkeypatch):
    _enable(monkeypatch)
    d = _scenario()
    with psycopg.connect(DSN) as db:
        _eligible(db, d["other"])
    url = f"/api/v1/dev/auto/vehicles/{d['vehicle']}/intake-assignments"
    http = _http()
    assert http.post(url, json={"userId":str(d["other"])},
                     headers={"X-User-ID":str(d["manager"])}).status_code == 403
    assert http.get(url).status_code == 404
    http.cookies.set("rp_session", d["tokens"][d["manager"]])
    assert http.post(url, json={"userId":str(d["other"])}).status_code == 403
    assert _act(http,d,d["intake"],url,json={"userId":str(d["other"])}).status_code == 404
    assert _act(http,d,d["manager"],url,json={"userId":str(d["manager"])}).status_code == 404
    assert _act(http,d,d["manager"],url,json={"userId":str(uuid4())}).status_code == 404
    assert _act(http,d,d["manager"],url,json={"userId":"bad"}).status_code == 422
    assert _act(http,d,d["manager"],url,json={"userId":str(d["other"]),"role":"MANAGER"}).status_code == 422
    given = _act(http,d,d["manager"],url,json={"userId":str(d["other"])})
    assert given.status_code == 201, given.text
    assigned = given.json()["assignmentId"]
    assert given.json()["status"] == "ACTIVE"
    assert given.headers["cache-control"] == "no-store"
    assert _act(http,d,d["manager"],url,json={"userId":str(d["other"])}).status_code == 409

    http.cookies.set("rp_session", d["tokens"][d["manager"]])
    listing = http.get(url)
    assert listing.status_code == 200
    assert {"assignmentId": assigned,"userId":str(d["other"])} in listing.json()["items"]
    assert "normalized_login" not in listing.text
    assert http.get(url,headers={"X-User-ID":str(d["other"])}).status_code == 200
    revoke = url + "/" + assigned + "/revoke"
    assert _act(http,d,d["other"],revoke).status_code == 404
    wrong_vehicle = uuid4()
    with psycopg.connect(DSN) as db:
        db.execute("INSERT INTO auto_vehicle(id,make,model) VALUES(%s,'Synthetic','Other')",
                   (wrong_vehicle,))
    wrong = f"/api/v1/dev/auto/vehicles/{wrong_vehicle}/intake-assignments/{assigned}/revoke"
    assert _act(http,d,d["manager"],wrong).status_code == 404
    assert _act(http,d,d["manager"],revoke).json()["status"] == "REVOKED"
    assert _act(http,d,d["manager"],revoke).json()["status"] == "ALREADY_REVOKED"
    http.cookies.set("rp_session", d["tokens"][d["manager"]])
    assert {"assignmentId":assigned,"userId":str(d["other"])} not in http.get(url).json()["items"]
    with psycopg.connect(DSN) as db:
        events = db.execute(
            """SELECT action,actor_id,target_user_id FROM auto_vehicle_staff_assignment_audit
               WHERE assignment_id=%s ORDER BY created_at""",(assigned,)
        ).fetchall()
        assert events == [("INTAKE_GRANTED",d["manager"],d["other"]),
                          ("INTAKE_REVOKED",d["manager"],d["other"])]
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("UPDATE auto_vehicle_staff_assignment_audit SET action='INTAKE_GRANTED' WHERE assignment_id=%s", (assigned,))
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("DELETE FROM auto_vehicle_staff_assignment_audit WHERE assignment_id=%s", (assigned,))
        assert db.execute("SELECT revoked_at FROM auto_vehicle_staff_assignment WHERE id=%s",
                          (assigned,)).fetchone()[0] is not None


def test_manager_revocation_role_grant_and_expired_subject(monkeypatch):
    _enable(monkeypatch)
    d = _scenario()
    with psycopg.connect(DSN) as db:
        _eligible(db, d["other"])
    url=f"/api/v1/dev/auto/vehicles/{d['vehicle']}/intake-assignments"
    http=_http()
    with psycopg.connect(DSN) as db:
        db.execute("""UPDATE auto_vehicle_staff_assignment SET revoked_at=now()
             WHERE vehicle_id=%s AND user_id=%s AND role_code='MANAGER'""",
                   (d["vehicle"],d["manager"]))
    assert _act(http,d,d["manager"],url,json={"userId":str(d["other"])}).status_code == 404
    http.cookies.set("rp_session",d["tokens"][d["manager"]])
    assert http.get(url).status_code == 404
    with psycopg.connect(DSN) as db:
        assert db.execute(
          "SELECT count(*) FROM auto_vehicle_staff_assignment_audit WHERE vehicle_id=%s",
          (d["vehicle"],)).fetchone()[0] == 0


def test_concurrent_grant_creates_one_assignment_and_one_audit(monkeypatch):
    _enable(monkeypatch)
    d=_scenario()
    with psycopg.connect(DSN) as db:
        _eligible(db,d["other"])
    def grant():
        with psycopg.connect(DSN) as db:
            try:
                return grant_intake(db,d["tokens"][d["manager"]],d["vehicle"],d["other"])
            except AssignmentConflict:
                return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        rows=list(pool.map(lambda _:grant(),range(2)))
    assert sum(result is not None for result in rows) == 1
    with psycopg.connect(DSN) as db:
        assert db.execute("""SELECT count(*) FROM auto_vehicle_staff_assignment
             WHERE vehicle_id=%s AND user_id=%s AND role_code='INTAKE'
               AND revoked_at IS NULL""", (d["vehicle"],d["other"])).fetchone()[0] == 1
        assert db.execute("""SELECT count(*) FROM auto_vehicle_staff_assignment_audit
             WHERE vehicle_id=%s AND target_user_id=%s AND action='INTAKE_GRANTED'""",
              (d["vehicle"],d["other"])).fetchone()[0] == 1
