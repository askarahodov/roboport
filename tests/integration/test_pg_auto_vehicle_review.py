"""Проверка привязки автомобиля: независимое staff-полномочие, CSRF, аудит."""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient

from roboport_api.main import app
from roboport_api.auto_vehicle_review import review_link
from roboport_api.session_tokens import new_token, token_digest
from test_pg_baseline import DSN


def _setup():
    staff, stranger, customer, vehicle, link, ticket = (uuid4() for _ in range(6))
    csrf, token, stranger_token = new_token(), new_token(), new_token()
    now = datetime.now(timezone.utc)
    with psycopg.connect(DSN) as db:
        for user in (staff, stranger):
            db.execute("INSERT INTO app_user(id,normalized_login) VALUES(%s,%s)",
                       (user, "review-" + user.hex))
        db.execute("INSERT INTO customer(id,label) VALUES(%s,%s)", (customer, customer.hex))
        db.execute("INSERT INTO auto_vehicle(id,make,model) VALUES(%s,'Synthetic','Van')",
                   (vehicle,))
        db.execute(
            "INSERT INTO auto_vehicle_customer_link(id,vehicle_id,customer_id) VALUES(%s,%s,%s)",
            (link, vehicle, customer))
        db.execute("INSERT INTO service_ticket(id,customer_id) VALUES(%s,%s)",
                   (ticket, customer))
        db.execute(
            "INSERT INTO user_role(id,user_id,role_code) VALUES(%s,%s,'INTAKE')",
            (uuid4(), staff))
        db.execute(
            """INSERT INTO acl_entry(id,user_id,ticket_id,action)
               VALUES(%s,%s,%s,'ticket.intake')""",
            (uuid4(), staff, ticket))
        for user, key, csrf_value in (
            (staff, token, csrf), (stranger, stranger_token, new_token())
        ):
            db.execute(
                """INSERT INTO user_session(id,user_id,token_hash,csrf_hash,created_at,
                   idle_expires_at,absolute_expires_at) VALUES(%s,%s,%s,%s,%s,%s,%s)""",
                (uuid4(), user, token_digest(key), token_digest(csrf_value), now,
                 now + timedelta(minutes=30), now + timedelta(hours=4)))
    return staff, stranger, customer, vehicle, link, ticket, token, stranger_token, csrf


def test_staff_review_uses_live_scoped_access_and_immutable_audit(monkeypatch):
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTO_API", "1")
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTH_API", "1")
    monkeypatch.setenv("ROBOport_DATABASE_URL", DSN)
    staff, stranger, customer, vehicle, link, ticket, token, other, csrf = _setup()
    endpoint = "/api/v1/dev/auto/links/" + str(link) + "/review"
    client = TestClient(app, base_url="https://127.0.0.1")
    payload = {"decision": "APPROVE", "evidenceRef": "synthetic-proof-001"}
    # Нельзя подделывать личность заголовками, нет CSRF или origin.
    assert client.post(endpoint, json=payload, headers={"X-User-ID":str(staff)}).status_code == 403
    client.cookies.set("rp_session", token)
    assert client.post(endpoint, json=payload).status_code == 403
    assert client.post(endpoint, json=payload, headers={"Origin":"https://127.0.0.1"}).status_code == 403
    assert client.post(endpoint, json=payload, headers={
        "Origin":"https://evil.invalid", "X-CSRF-Token":csrf}).status_code == 403
    wrong = TestClient(app, base_url="https://127.0.0.1")
    wrong.cookies.set("rp_session", other)
    assert wrong.post(endpoint, json=payload, headers={
        "Origin":"https://127.0.0.1", "X-CSRF-Token":csrf}).status_code == 403
    assert client.post(endpoint, json=payload, headers={
        "Origin":"https://127.0.0.1", "X-CSRF-Token":csrf}).status_code == 200
    # Повторная отправка и конкурентное решение не изменяют уже принятого решения.
    assert client.post(endpoint, json=payload, headers={
        "Origin":"https://127.0.0.1", "X-CSRF-Token":csrf}).status_code == 409
    with psycopg.connect(DSN) as db:
        record = db.execute(
            "SELECT verified_by,verified_at FROM auto_vehicle_customer_link WHERE id=%s",
            (link,)).fetchone()
        assert record[0] == staff and record[1] is not None
        audits = db.execute(
            """SELECT reviewer_id,decision,evidence_ref FROM auto_vehicle_link_review
               WHERE link_id=%s""", (link,)).fetchall()
        assert audits == [(staff, "APPROVE", "synthetic-proof-001")]
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("DELETE FROM auto_vehicle_link_review WHERE link_id=%s", (link,))


def test_role_and_acl_revocations_block_review(monkeypatch):
    staff, stranger, customer, vehicle, link, ticket, token, other, csrf = _setup()
    with psycopg.connect(DSN) as db:
        db.execute(
            """UPDATE acl_entry SET revoked_at=now()
               WHERE user_id=%s AND ticket_id=%s AND action='ticket.intake'""",
            (staff,ticket))
    with psycopg.connect(DSN) as db:
        assert review_link(db, token, link, "APPROVE", "synthetic-002") == "DENIED"
    with psycopg.connect(DSN) as db:
        assert db.execute(
            "SELECT verified_at FROM auto_vehicle_customer_link WHERE id=%s",
            (link,)).fetchone()[0] is None
        assert db.execute(
            "SELECT count(*) FROM auto_vehicle_link_review WHERE link_id=%s",
            (link,)).fetchone()[0] == 0


def test_reject_revoke_ends_pending_claim():
    staff, stranger, customer, vehicle, link, ticket, token, other, csrf = _setup()
    with psycopg.connect(DSN) as db:
        assert review_link(db, token, link, "REJECT", "synthetic-003") == "REJECTED"
    with psycopg.connect(DSN) as db:
        assert db.execute("SELECT revoked_at FROM auto_vehicle_customer_link WHERE id=%s",
                          (link,)).fetchone()[0] is not None
        assert review_link(db, token, link, "APPROVE", "synthetic-003") == "DENIED"
