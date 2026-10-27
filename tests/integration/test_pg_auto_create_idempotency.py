"""PG17: HTTP retry с тем же ключом не повторяет изменение или аудит."""
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient

from roboport_api.auto_intake_delegation import grant_intake
from roboport_api.auto_create_idempotency import validated_key
from roboport_api.main import app
from test_pg_auto_pending_link_http import _scene, _flags
from test_pg_auto_vehicle_claim_http import _scenario
from test_pg_baseline import DSN


def _client(s, actor):
    http = TestClient(app, base_url="https://127.0.0.1")
    http.cookies.set("rp_session", s["tokens"][actor])
    headers = {
        "Origin": "https://127.0.0.1",
        "X-CSRF-Token": s["csrf"][actor],
    }
    return http, headers


def test_pending_link_http_retry_conflict_and_live_revocation(monkeypatch):
    _flags(monkeypatch)
    s = _scene()
    http, normal = _client(s, s["intake"])
    url = f"/api/v1/dev/auto/vehicles/{s['vehicle']}/links"
    key = uuid4().hex
    headers = {**normal, "Idempotency-Key": key}
    payload = {"customerId": str(s["customer"])}
    first = http.post(url, json=payload, headers=headers)
    assert first.status_code == 201, first.text
    retry = http.post(url, json=payload, headers=headers)
    assert retry.status_code == 201 and retry.json() == first.json()
    assert retry.headers["cache-control"] == "no-store"
    # Другая разрешённая customer-пара с тем же ключом не может получить receipt.
    other = http.post(url, json={"customerId": str(s["unrelated"])}, headers=headers)
    assert other.status_code == 409
    for bad in ("", "short", "x" * 129, "has space"):
        invalid = http.post(url, json=payload,
                            headers={**normal, "Idempotency-Key": bad})
        assert invalid.status_code == 422
    # Не-ASCII HTTP header отбрасывается httpx до транспорта, поэтому
    # валидируем этот класс входных значений непосредственно в сервисе.
    with pytest.raises(ValueError):
        validated_key("ä" * 20)
    with psycopg.connect(DSN) as db:
        db.execute(
            "UPDATE user_role SET revoked_at=now() WHERE user_id=%s AND role_code='INTAKE'",
            (s["intake"],),
        )
    assert http.post(url, json=payload, headers=headers).status_code == 403
    with psycopg.connect(DSN) as db:
        link = first.json()["linkId"]
        assert db.execute("SELECT count(*) FROM auto_vehicle_link_intake_audit WHERE link_id=%s",
                          (link,)).fetchone()[0] == 1
        receipt = db.execute("SELECT key_hash FROM auto_create_receipt WHERE result_id=%s",
                             (link,)).fetchone()
        assert receipt and receipt[0] != key and len(receipt[0]) == 64
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("DELETE FROM auto_create_receipt WHERE result_id=%s", (link,))


def test_manager_grant_retry_and_revoked_receipt(monkeypatch):
    _flags(monkeypatch)
    d = _scenario()
    with psycopg.connect(DSN) as db:
        db.execute("INSERT INTO user_role(id,user_id,role_code) VALUES(%s,%s,'INTAKE')",
                   (uuid4(), d["other"]))
    http, normal = _client(d, d["manager"])
    url = f"/api/v1/dev/auto/vehicles/{d['vehicle']}/intake-assignments"
    key = uuid4().hex
    headers = {**normal, "Idempotency-Key": key}
    payload = {"userId": str(d["other"])}
    first = http.post(url, json=payload, headers=headers)
    assert first.status_code == 201, first.text
    retry = http.post(url, json=payload, headers=headers)
    assert retry.status_code == 201 and retry.json() == first.json()
    assigned = first.json()["assignmentId"]
    assert http.post(url, json=payload, headers={
        **normal, "Idempotency-Key": uuid4().hex,
    }).status_code == 409
    with psycopg.connect(DSN) as db:
        assert db.execute(
            """SELECT count(*) FROM auto_vehicle_staff_assignment_audit
               WHERE assignment_id=%s AND action='INTAKE_GRANTED'""",
            (assigned,),
        ).fetchone()[0] == 1
    revoke = url + "/" + assigned + "/revoke"
    assert http.post(revoke, headers=normal).status_code == 200
    # Не возвращать устаревший успех после отзыва назначения.
    assert http.post(url, json=payload, headers=headers).status_code == 409
    with psycopg.connect(DSN) as db:
        assert db.execute(
            "SELECT count(*) FROM auto_create_receipt WHERE result_id=%s",
            (assigned,),
        ).fetchone()[0] == 1


def test_concurrent_identical_key_one_grant_and_audit(monkeypatch):
    _flags(monkeypatch)
    d = _scenario()
    with psycopg.connect(DSN) as db:
        db.execute("INSERT INTO user_role(id,user_id,role_code) VALUES(%s,%s,'INTAKE')",
                   (uuid4(), d["other"]))
    key = uuid4().hex

    def create(_):
        with psycopg.connect(DSN) as db:
            return grant_intake(
                db, d["tokens"][d["manager"]], d["vehicle"], d["other"],
                idempotency_key=key,
            )

    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(create, range(2)))
    assert ids[0] == ids[1]
    with psycopg.connect(DSN) as db:
        assert db.execute(
            """SELECT count(*) FROM auto_vehicle_staff_assignment_audit
               WHERE assignment_id=%s AND action='INTAKE_GRANTED'""",
            (ids[0],),
        ).fetchone()[0] == 1
        assert db.execute(
            "SELECT count(*) FROM auto_create_receipt WHERE result_id=%s",
            (ids[0],),
        ).fetchone()[0] == 1
