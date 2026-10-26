"""Интеграционные HTTP-проверки приватного каталога машин на PostgreSQL 17.

Используются исключительно синтетические пользователи и VIN.
"""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import psycopg
from fastapi.testclient import TestClient

from roboport_api.main import app
from roboport_api.session_tokens import new_token, token_digest
from test_pg_baseline import DSN


def _seed():
    a, b, reviewer = uuid4(), uuid4(), uuid4()
    ca, cb = uuid4(), uuid4()
    va, vb, pending = uuid4(), uuid4(), uuid4()
    tokens = new_token(), new_token()
    now = datetime.now(timezone.utc)
    with psycopg.connect(DSN) as conn:
        for user in (a, b, reviewer):
            conn.execute(
                "INSERT INTO app_user(id,normalized_login) VALUES(%s,%s)",
                (user, "auto-test-" + user.hex),
            )
        for user, token in zip((a, b), tokens):
            conn.execute(
                """INSERT INTO user_session(
                    id,user_id,token_hash,csrf_hash,created_at,
                    idle_expires_at,absolute_expires_at
                ) VALUES(%s,%s,%s,%s,%s,%s,%s)""",
                (uuid4(), user, token_digest(token), token_digest(new_token()),
                 now, now + timedelta(minutes=30), now + timedelta(hours=4)),
            )
            conn.execute(
                "INSERT INTO user_role(id,user_id,role_code) VALUES(%s,%s,'CLIENT')",
                (uuid4(), user),
            )
        for customer in (ca, cb):
            conn.execute("INSERT INTO customer(id,label) VALUES(%s,%s)", (customer, customer.hex))
        for user, customer in ((a, ca), (b, cb)):
            conn.execute(
                "INSERT INTO account_customer_link(user_id,customer_id) VALUES(%s,%s)",
                (user, customer),
            )
        for vehicle, model, vin in (
            (va, "Sedan", "TEST0000000000001"),
            (vb, "SUV", "TEST0000000000002"),
            (pending, "Hidden", None),
        ):
            conn.execute(
                "INSERT INTO auto_vehicle(id,make,model,vin,model_year) VALUES(%s,'TestMake',%s,%s,2026)",
                (vehicle, model, vin),
            )
        for vehicle, customer in ((va, ca), (vb, cb)):
            conn.execute(
                """INSERT INTO auto_vehicle_customer_link(
                    id,vehicle_id,customer_id,verified_at,verified_by
                ) VALUES(%s,%s,%s,now(),%s)""",
                (uuid4(), vehicle, customer, reviewer),
            )
        conn.execute(
            """INSERT INTO auto_vehicle_customer_link(id,vehicle_id,customer_id)
               VALUES(%s,%s,%s)""",
            (uuid4(), pending, ca),
        )
    return a, b, ca, cb, va, vb, pending, tokens


def test_auto_vehicle_only_verified_client_records(monkeypatch):
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTO_API", "1")
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTH_API", "1")
    monkeypatch.setenv("ROBOport_DATABASE_URL", DSN)
    a, b, ca, cb, va, vb, pending, (token_a, token_b) = _seed()
    endpoint = "/api/v1/dev/auto/vehicles"
    client = TestClient(app, base_url="https://127.0.0.1")

    # Не доверять X-User-ID или X-Customer-ID без cookie-сессии.
    denied = client.get(endpoint, headers={"X-User-ID": str(a), "X-Customer-ID": str(ca)})
    assert denied.status_code == 401
    client.cookies.set("rp_session", token_a)
    listing = client.get(endpoint)
    assert listing.status_code == 200
    assert {item["id"] for item in listing.json()["items"]} == {str(va)}
    assert listing.headers["cache-control"] == "no-store"
    assert listing.json()["items"][0]["vinLast4"] == "0001"
    assert "vin" not in listing.json()["items"][0]
    assert client.get(endpoint + "/" + str(va)).status_code == 200
    assert client.get(endpoint + "/" + str(vb)).status_code == 404
    assert client.get(endpoint + "/" + str(pending)).status_code == 404
    assert client.get(endpoint + "/" + "not-a-uuid").status_code == 404
    assert client.get(endpoint + "?limit=0").status_code == 422

    another = TestClient(app, base_url="https://127.0.0.1")
    another.cookies.set("rp_session", token_b)
    assert {v["id"] for v in another.get(endpoint).json()["items"]} == {str(vb)}
    assert another.get(endpoint + "/" + str(va)).status_code == 404

    # Отзыв владения автомобилем и клиентской связи немедленно сужает доступ.
    with psycopg.connect(DSN) as conn:
        conn.execute(
            "UPDATE auto_vehicle_customer_link SET revoked_at=now() WHERE vehicle_id=%s",
            (va,),
        )
    assert client.get(endpoint).json()["items"] == []
    with psycopg.connect(DSN) as conn:
        conn.execute(
            "UPDATE account_customer_link SET revoked_at=now() WHERE user_id=%s",
            (b,),
        )
    assert another.get(endpoint).json()["items"] == []

    # Отозванная сессия → 401; отсутствие флага → 404.
    with psycopg.connect(DSN) as conn:
        conn.execute(
            "UPDATE user_session SET revoked_at=now() WHERE token_hash=%s",
            (token_digest(token_a),),
        )
    assert client.get(endpoint).status_code == 401
    monkeypatch.delenv("ROBOport_SYNTHETIC_AUTO_API")
    assert client.get(endpoint).status_code == 404


def test_auto_vehicle_requires_private_local_transport(monkeypatch):
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTO_API", "1")
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTH_API", "1")
    monkeypatch.setenv("ROBOport_DATABASE_URL", DSN)
    client = TestClient(app, base_url="http://127.0.0.1")
    assert client.get("/api/v1/dev/auto/vehicles").status_code == 404
