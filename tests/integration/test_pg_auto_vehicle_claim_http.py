"""Реальный PostgreSQL: proof/review/ACL и отмена доступа без наследования истории."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient

from roboport_api.auto_vehicle_claim import (
    VehicleClaimRejected, review_auto_claim,
)
from roboport_api.main import app
from roboport_api.session_tokens import new_token, token_digest
from test_pg_baseline import DSN


def _scenario():
    client_a, client_b, intake, manager, other = (uuid4() for _ in range(5))
    ca, cb, vehicle, pending = (uuid4() for _ in range(4))
    link, other_link, old_ticket = uuid4(), uuid4(), uuid4()
    tokens = {u: new_token() for u in (client_a, client_b, intake, manager, other)}
    csrf = {u: new_token() for u in tokens}
    now = datetime.now(timezone.utc)
    with psycopg.connect(DSN) as db:
        for user in tokens:
            db.execute(
                "INSERT INTO app_user(id,normalized_login) VALUES(%s,%s)",
                (user, "auto-claim-" + user.hex),
            )
            role = (
                "CLIENT" if user in (client_a, client_b)
                else "INTAKE" if user == intake else "MANAGER"
            )
            db.execute(
                "INSERT INTO user_role(id,user_id,role_code) VALUES(%s,%s,%s)",
                (uuid4(), user, role),
            )
            db.execute(
                """INSERT INTO user_session(
                  id,user_id,token_hash,csrf_hash,created_at,
                  idle_expires_at,absolute_expires_at
                ) VALUES(%s,%s,%s,%s,%s,%s,%s)""",
                (uuid4(), user, token_digest(tokens[user]), token_digest(csrf[user]),
                 now, now + timedelta(minutes=30), now + timedelta(hours=4)),
            )
        for customer in (ca, cb):
            db.execute(
                "INSERT INTO customer(id,label) VALUES(%s,%s)",
                (customer, "auto-" + customer.hex),
            )
        for user, customer in ((client_a, ca), (client_b, cb)):
            db.execute(
                "INSERT INTO account_customer_link(user_id,customer_id) VALUES(%s,%s)",
                (user, customer),
            )
        db.execute(
            "INSERT INTO service_ticket(id,customer_id) VALUES(%s,%s)",
            (old_ticket, ca),
        )
        db.execute(
            "INSERT INTO auto_vehicle(id,make,model) VALUES(%s,'Synthetic','Sedan')",
            (vehicle,),
        )
        for ident, customer in ((link, ca), (other_link, cb)):
            db.execute(
                "INSERT INTO auto_vehicle_customer_link(id,vehicle_id,customer_id) VALUES(%s,%s,%s)",
                (ident, vehicle, customer),
            )
        for actor, role in ((intake, "INTAKE"), (manager, "MANAGER")):
            db.execute(
                """INSERT INTO auto_vehicle_staff_assignment(id,vehicle_id,user_id,role_code)
                   VALUES(%s,%s,%s,%s)""",
                (uuid4(), vehicle, actor, role),
            )
    return locals()


def _http():
    return TestClient(app, base_url="https://127.0.0.1")


def _act(http, data, user, path, **kwargs):
    http.cookies.set("rp_session", data["tokens"][user])
    headers = {
        "Origin": "https://127.0.0.1",
        "X-CSRF-Token": data["csrf"][user],
    }
    return http.post(path, headers=headers, **kwargs)


def test_independent_auto_claim_and_revocation(monkeypatch):
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTO_API", "1")
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTH_API", "1")
    monkeypatch.setenv("ROBOport_SYNTHETIC_ACCESS_API", "1")
    monkeypatch.setenv("ROBOport_DATABASE_URL", DSN)
    d = _scenario()
    http = _http()
    start = f"/api/v1/dev/auto/links/{d['link']}/issue-proof"
    list_url = "/api/v1/dev/auto/vehicles"
    owner = d["client_a"]

    # Нет cookie/CSRF/role/scope — нельзя выдавать доказательство.
    assert http.post(start, headers={"X-User-ID": str(d["intake"])}).status_code == 403
    http.cookies.set("rp_session", d["tokens"][d["intake"]])
    assert http.post(start).status_code == 403
    assert _act(http, d, d["manager"], start).status_code == 403
    assert _act(http, d, d["other"], start).status_code == 403
    result = _act(http, d, d["intake"], start)
    assert result.status_code == 200, result.text
    assert result.headers["cache-control"] == "no-store"
    claim, proof = result.json()["claimId"], result.json()["proof"]
    assert len(proof) >= 40
    assert _act(http, d, d["intake"], start).status_code == 403

    redeem = f"/api/v1/dev/auto/claims/{claim}/redeem"
    review = f"/api/v1/dev/auto/claims/{claim}/review"
    # Другой клиент не может подтвердить эту связку, даже с доказательством.
    assert _act(http, d, d["client_b"], redeem, json={"proof": proof}).status_code == 403
    wrong = _act(http, d, owner, redeem, json={"proof": "bad" * 15})
    assert wrong.status_code == 200 and wrong.json()["status"] == "REJECTED"
    http.cookies.set("rp_session", d["tokens"][owner])
    assert http.get(list_url).json()["items"] == []
    redeemed = _act(http, d, owner, redeem, json={"proof": proof})
    assert redeemed.status_code == 200 and redeemed.json()["status"] == "PENDING_REVIEW"
    assert _act(http, d, owner, redeem, json={"proof": proof}).status_code == 403
    assert http.get(list_url).json()["items"] == []
    assert _act(http, d, owner, review, json={"approve": True}).status_code == 403
    assert _act(http, d, d["other"], review, json={"approve": True}).status_code == 403
    assert _act(http, d, d["intake"], review, json={"approve": True}).status_code == 403

    accepted = _act(http, d, d["manager"], review, json={"approve": True})
    assert accepted.status_code == 200 and accepted.json()["status"] == "VERIFIED"
    assert _act(http, d, d["manager"], review, json={"approve": True}).status_code == 403
    http.cookies.set("rp_session", d["tokens"][owner])
    response = http.get(list_url)
    assert response.status_code == 200
    assert [v["id"] for v in response.json()["items"]] == [str(d["vehicle"])]
    assert "vin" not in response.text.lower()
    http.cookies.set("rp_session", d["tokens"][d["client_b"]])
    assert http.get(list_url).json()["items"] == []

    # Второй покупатель может владеть авто, но не получает старые ticket records.
    with psycopg.connect(DSN) as db:
        db.execute(
            """UPDATE auto_vehicle_customer_link SET verified_at=now(),verified_by=%s
               WHERE id=%s""", (d["manager"], d["other_link"]),
        )
    assert [v["id"] for v in http.get(list_url).json()["items"]] == [str(d["vehicle"])]
    assert http.get(f"/api/v1/dev/tickets/{d['old_ticket']}").status_code in (403, 404)
    # Manager отзывает только тот объект, на который явно назначен.
    revoked = _act(
        http, d, d["manager"], f"/api/v1/dev/auto/links/{d['link']}/revoke",
    )
    assert revoked.status_code == 200 and revoked.json()["status"] == "REVOKED"
    http.cookies.set("rp_session", d["tokens"][owner])
    assert http.get(list_url).json()["items"] == []
    with psycopg.connect(DSN) as db:
        audit = db.execute(
            "SELECT action FROM auto_vehicle_claim_audit WHERE link_id=%s ORDER BY created_at,id",
            (d["link"],),
        ).fetchall()
        assert sorted(a[0] for a in audit) == sorted(
            ("ISSUED", "PROOF_REJECTED", "PROOF_ACCEPTED", "VERIFIED", "REVOKED")
        )
        assert db.execute(
            "SELECT proof_hash FROM auto_vehicle_claim WHERE id=%s", (claim,),
        ).fetchone()[0] != proof
        assert db.execute(
            "SELECT verified_by FROM auto_vehicle_customer_link WHERE id=%s",
            (d["link"],),
        ).fetchone()[0] == d["manager"]
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute(
                    "DELETE FROM auto_vehicle_claim_audit WHERE link_id=%s",
                    (d["link"],),
                )


def test_auto_claim_review_requires_live_role_and_scope(monkeypatch):
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTO_API", "1")
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTH_API", "1")
    monkeypatch.setenv("ROBOport_DATABASE_URL", DSN)
    d = _scenario()
    http = _http()
    result = _act(
        http, d, d["intake"],
        f"/api/v1/dev/auto/links/{d['link']}/issue-proof",
    )
    claim, proof = result.json()["claimId"], result.json()["proof"]
    assert _act(
        http, d, d["client_a"], f"/api/v1/dev/auto/claims/{claim}/redeem",
        json={"proof": proof},
    ).status_code == 200
    review = f"/api/v1/dev/auto/claims/{claim}/review"
    with psycopg.connect(DSN) as db:
        db.execute(
            """UPDATE auto_vehicle_staff_assignment SET revoked_at=now()
               WHERE vehicle_id=%s AND user_id=%s""",
            (d["vehicle"], d["manager"]),
        )
    assert _act(http, d, d["manager"], review, json={"approve": True}).status_code == 403
    with psycopg.connect(DSN) as db:
        db.execute(
            """INSERT INTO auto_vehicle_staff_assignment(id,vehicle_id,user_id,role_code)
               VALUES(%s,%s,%s,'MANAGER')""",
            (uuid4(), d["vehicle"], d["manager"]),
        )
        db.execute(
            "UPDATE user_role SET revoked_at=now() WHERE user_id=%s",
            (d["manager"],),
        )
    assert _act(http, d, d["manager"], review, json={"approve": True}).status_code == 403
    with psycopg.connect(DSN) as db:
        assert db.execute(
            "SELECT verified_at FROM auto_vehicle_customer_link WHERE id=%s",
            (d["link"],),
        ).fetchone()[0] is None


def test_auto_claim_concurrent_review_is_single_use(monkeypatch):
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTO_API", "1")
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTH_API", "1")
    monkeypatch.setenv("ROBOport_DATABASE_URL", DSN)
    d = _scenario()
    http = _http()
    issued = _act(http, d, d["intake"], f"/api/v1/dev/auto/links/{d['link']}/issue-proof")
    claim, proof = issued.json()["claimId"], issued.json()["proof"]
    assert _act(
        http, d, d["client_a"], f"/api/v1/dev/auto/claims/{claim}/redeem",
        json={"proof": proof},
    ).status_code == 200

    def attempt(_):
        try:
            with psycopg.connect(DSN) as db:
                return review_auto_claim(db, d["tokens"][d["manager"]], claim, True)
        except VehicleClaimRejected:
            return "REJECTED"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = sorted(pool.map(attempt, (1, 2)))
    assert outcomes == ["REJECTED", "VERIFIED"]
    with psycopg.connect(DSN) as db:
        assert db.execute(
            """SELECT count(*) FROM auto_vehicle_claim_audit
               WHERE claim_id=%s AND action='VERIFIED'""",
            (claim,),
        ).fetchone()[0] == 1


def test_auto_claim_opt_in_and_https_only(monkeypatch):
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTO_API", "1")
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTH_API", "1")
    monkeypatch.setenv("ROBOport_DATABASE_URL", DSN)
    http = TestClient(app, base_url="http://127.0.0.1")
    assert http.post(f"/api/v1/dev/auto/links/{uuid4()}/issue-proof").status_code == 404
    monkeypatch.delenv("ROBOport_SYNTHETIC_AUTO_API")
    https = _http()
    assert https.post(f"/api/v1/dev/auto/links/{uuid4()}/issue-proof").status_code == 404
