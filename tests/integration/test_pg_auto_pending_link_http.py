"""Синтетическая приёмка pending-привязки и сквозной независимый review, PG17."""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient

from roboport_api.main import app
from roboport_api.session_tokens import new_token, token_digest
from test_pg_baseline import DSN


def _scene():
    client, outsider, intake, manager = (uuid4() for _ in range(4))
    customer, unrelated, vehicle, another_vehicle = (uuid4() for _ in range(4))
    tokens = {user: new_token() for user in (client, outsider, intake, manager)}
    csrf = {user: new_token() for user in tokens}
    now = datetime.now(timezone.utc)
    with psycopg.connect(DSN) as db:
        for user, role in ((client, "CLIENT"), (outsider, "CLIENT"),
                           (intake, "INTAKE"), (manager, "MANAGER")):
            db.execute("INSERT INTO app_user(id,normalized_login) VALUES(%s,%s)",
                       (user, "link-propose-" + user.hex))
            db.execute("INSERT INTO user_role(id,user_id,role_code) VALUES(%s,%s,%s)",
                       (uuid4(), user, role))
            db.execute(
                """INSERT INTO user_session(id,user_id,token_hash,csrf_hash,created_at,
                   idle_expires_at,absolute_expires_at) VALUES(%s,%s,%s,%s,%s,%s,%s)""",
                (uuid4(), user, token_digest(tokens[user]), token_digest(csrf[user]),
                 now, now + timedelta(minutes=30), now + timedelta(hours=4)),
            )
        for customer_id in (customer, unrelated):
            db.execute("INSERT INTO customer(id,label) VALUES(%s,%s)",
                       (customer_id, "synthetic-" + customer_id.hex))
        db.execute("INSERT INTO account_customer_link(user_id,customer_id) VALUES(%s,%s)",
                   (client, customer))
        db.execute("INSERT INTO account_customer_link(user_id,customer_id) VALUES(%s,%s)",
                   (outsider, unrelated))
        for ident in (vehicle, another_vehicle):
            db.execute("INSERT INTO auto_vehicle(id,make,model) VALUES(%s,'Test','Van')",
                       (ident,))
        for staff, role in ((intake, "INTAKE"), (manager, "MANAGER")):
            db.execute("""INSERT INTO auto_vehicle_staff_assignment
                       (id,vehicle_id,user_id,role_code) VALUES(%s,%s,%s,%s)""",
                       (uuid4(), vehicle, staff, role))
    return locals()


def _post(scene, person, url, **kwargs):
    http = TestClient(app, base_url="https://127.0.0.1")
    http.cookies.set("rp_session", scene["tokens"][person])
    headers = {"Origin": "https://127.0.0.1", "X-CSRF-Token": scene["csrf"][person]}
    return http.post(url, headers=headers, **kwargs)


def _flags(monkeypatch):
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTO_API", "1")
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTH_API", "1")
    monkeypatch.setenv("ROBOport_DATABASE_URL", DSN)


def test_intake_pending_link_independent_proof_review_and_audit(monkeypatch):
    _flags(monkeypatch)
    s = _scene()
    path = f"/api/v1/dev/auto/vehicles/{s['vehicle']}/links"
    payload = {"customerId": str(s["customer"])}
    # Даже тот же customer не может сам выдать связь, если нет назначения INTAKE.
    assert _post(s, s["client"], path, json=payload).status_code == 403
    assert _post(s, s["manager"], path, json=payload).status_code == 403
    assert _post(s, s["intake"], f"/api/v1/dev/auto/vehicles/{s['another_vehicle']}/links",
                 json=payload).status_code == 403
    created = _post(s, s["intake"], path, json=payload)
    assert created.status_code == 201, created.text
    assert created.json()["status"] == "PENDING"
    assert created.headers["cache-control"] == "no-store"
    link = created.json()["linkId"]
    # Повторное создание заблокировано уникальным активным индексом.
    assert _post(s, s["intake"], path, json=payload).status_code == 409
    owner = TestClient(app, base_url="https://127.0.0.1")
    owner.cookies.set("rp_session", s["tokens"][s["client"]])
    assert owner.get("/api/v1/dev/auto/vehicles").json()["items"] == []
    issued = _post(s, s["intake"], f"/api/v1/dev/auto/links/{link}/issue-proof")
    assert issued.status_code == 200
    claim = issued.json()["claimId"]
    proof = issued.json()["proof"]
    redeemed = _post(s, s["client"], f"/api/v1/dev/auto/claims/{claim}/redeem",
                     json={"proof": proof})
    assert redeemed.status_code == 200 and redeemed.json()["status"] == "PENDING_REVIEW"
    assert owner.get("/api/v1/dev/auto/vehicles").json()["items"] == []
    approved = _post(s, s["manager"], f"/api/v1/dev/auto/claims/{claim}/review",
                     json={"approve": True})
    assert approved.status_code == 200 and approved.json()["status"] == "VERIFIED"
    assert [item["id"] for item in owner.get("/api/v1/dev/auto/vehicles").json()["items"]] == [
        str(s["vehicle"])
    ]
    with psycopg.connect(DSN) as db:
        record = db.execute(
            """SELECT actor_id,action FROM auto_vehicle_link_intake_audit
               WHERE link_id=%s""", (link,),
        ).fetchone()
        assert record == (s["intake"], "LINK_CREATED")
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("DELETE FROM auto_vehicle_link_intake_audit WHERE link_id=%s",
                           (link,))


def test_pending_link_requires_live_scope_csrf_active_client_and_local_transport(monkeypatch):
    _flags(monkeypatch)
    s = _scene()
    endpoint = f"/api/v1/dev/auto/vehicles/{s['vehicle']}/links"
    data = {"customerId": str(s["customer"])}
    client = TestClient(app, base_url="https://127.0.0.1")
    assert client.post(endpoint, json=data, headers={"X-User-ID": str(s["intake"])}).status_code == 403
    client.cookies.set("rp_session", s["tokens"][s["intake"]])
    assert client.post(endpoint, json=data).status_code == 403
    assert client.post(endpoint, json=data,
                       headers={"Origin": "https://127.0.0.1"}).status_code == 403
    assert client.post(endpoint, json=data, headers={
        "Origin": "https://evil.invalid",
        "X-CSRF-Token": s["csrf"][s["intake"]],
    }).status_code == 403
    for bad in ({"customerId": str(uuid4())}, {"customerId": str(s["customer"]),
                                              "verified": True}):
        assert _post(s, s["intake"], endpoint, json=bad).status_code in (403, 422)
    insecure = TestClient(app, base_url="http://127.0.0.1")
    assert insecure.post(endpoint, json=data).status_code == 404
    with psycopg.connect(DSN) as db:
        db.execute("UPDATE auto_vehicle_staff_assignment SET revoked_at=now() WHERE user_id=%s",
                   (s["intake"],))
    assert _post(s, s["intake"], endpoint, json=data).status_code == 403
    with psycopg.connect(DSN) as db:
        assert db.execute(
            "SELECT count(*) FROM auto_vehicle_customer_link WHERE vehicle_id=%s",
            (s["vehicle"],)).fetchone()[0] == 0
    monkeypatch.delenv("ROBOport_SYNTHETIC_AUTO_API")
    assert _post(s, s["intake"], endpoint, json=data).status_code == 404
