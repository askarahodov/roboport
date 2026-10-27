"""PG17: общий rate-limit авто-доказательств по actor, поверх 5 попыток/claim."""
from uuid import uuid4

import psycopg
from test_pg_auto_claim_retry_http import _flags, _issued, _post
from test_pg_auto_vehicle_claim_http import _scenario
from test_pg_baseline import DSN


def _new_link(data):
    vehicle, link = uuid4(), uuid4()
    with psycopg.connect(DSN) as db:
        db.execute(
            "INSERT INTO auto_vehicle(id,make,model) VALUES(%s,'Synthetic','Throttle')",
            (vehicle,),
        )
        db.execute(
            """INSERT INTO auto_vehicle_customer_link(id,vehicle_id,customer_id)
               VALUES(%s,%s,%s)""",
            (link, vehicle, data["ca"]),
        )
        db.execute(
            """INSERT INTO auto_vehicle_staff_assignment(id,vehicle_id,user_id,role_code)
               VALUES(%s,%s,%s,'INTAKE')""",
            (uuid4(), vehicle, data["intake"]),
        )
    return link


def test_redeem_actor_budget_across_claims_and_keys(monkeypatch):
    _flags(monkeypatch)
    d = _scenario()
    claim_a, _ = _issued(d)
    second_link = _new_link(d)
    second_issue = _post(
        d, d["intake"], f"/api/v1/dev/auto/links/{second_link}/issue-proof", {},
    )
    assert second_issue.status_code == 200, second_issue.text
    claim_b = second_issue.json()["claimId"]
    endpoint_a = f"/api/v1/dev/auto/claims/{claim_a}/redeem"
    endpoint_b = f"/api/v1/dev/auto/claims/{claim_b}/redeem"
    shared_key = uuid4().hex
    for i in range(5):
        result = _post(
            d, d["client_a"], endpoint_a, {"proof": "incorrect" * 5},
            shared_key if i == 0 else uuid4().hex,
        )
        assert result.status_code == 200 and result.json()["status"] == "REJECTED"
    for i in range(3):
        result = _post(
            d, d["client_a"], endpoint_b, {"proof": "incorrect" * 5},
            uuid4().hex,
        )
        assert result.status_code == 200 and result.json()["status"] == "REJECTED"
    denied = _post(d, d["client_a"], endpoint_b,
                   {"proof": "incorrect" * 5}, uuid4().hex)
    assert denied.status_code == 429
    assert denied.json()["code"] == "RATE_LIMITED"
    assert denied.headers["cache-control"] == "no-store"
    assert denied.headers["retry-after"] == "900"
    with psycopg.connect(DSN) as db:
        assert db.execute(
            """SELECT attempts FROM auto_proof_throttle
               WHERE actor_id=%s AND operation='REDEEM'""",
            (d["client_a"],),
        ).fetchone()[0] == 9
        assert db.execute(
            "SELECT proof_attempts FROM auto_vehicle_claim WHERE id=%s",
            (claim_b,),
        ).fetchone()[0] == 3
        assert db.execute(
            """SELECT count(*) FROM auto_vehicle_claim_audit
               WHERE claim_id=%s AND action='PROOF_REJECTED'""",
            (claim_b,),
        ).fetchone()[0] == 3
    # Другая клиентская учётная запись имеет независимый бюджет.
    another = _post(
        d, d["intake"], f"/api/v1/dev/auto/links/{d['other_link']}/issue-proof", {},
    )
    assert another.status_code == 200
    other_result = _post(
        d, d["client_b"],
        f"/api/v1/dev/auto/claims/{another.json()['claimId']}/redeem",
        {"proof": "incorrect" * 5}, uuid4().hex,
    )
    assert other_result.status_code == 200
    # Имитация истечения окна без ожидания реального времени.
    with psycopg.connect(DSN) as db:
        db.execute(
            """UPDATE auto_proof_throttle
               SET window_started_at=now()-interval '16 minutes'
               WHERE actor_id=%s AND operation='REDEEM'""",
            (d["client_a"],),
        )
    recovered = _post(d, d["client_a"], endpoint_b,
                      {"proof": "incorrect" * 5}, uuid4().hex)
    assert recovered.status_code == 200
    with psycopg.connect(DSN) as db:
        assert db.execute(
            """SELECT attempts FROM auto_proof_throttle
               WHERE actor_id=%s AND operation='REDEEM'""",
            (d["client_a"],),
        ).fetchone()[0] == 1


def test_issue_budget_twelve_across_links_with_safe_429(monkeypatch):
    _flags(monkeypatch)
    d = _scenario()
    links = [d["link"], d["other_link"]]
    links.extend(_new_link(d) for _ in range(11))
    for link in links[:12]:
        issued = _post(
            d, d["intake"], f"/api/v1/dev/auto/links/{link}/issue-proof", {},
        )
        assert issued.status_code == 200 and len(issued.json()["proof"]) >= 40
    denied = _post(
        d, d["intake"], f"/api/v1/dev/auto/links/{links[12]}/issue-proof", {},
    )
    assert denied.status_code == 429
    assert denied.json()["code"] == "RATE_LIMITED"
    assert denied.headers["retry-after"] == "3600"
    assert "proof" not in denied.json()
    with psycopg.connect(DSN) as db:
        assert db.execute(
            """SELECT attempts FROM auto_proof_throttle
               WHERE actor_id=%s AND operation='ISSUE'""",
            (d["intake"],),
        ).fetchone()[0] == 13
        assert db.execute(
            "SELECT count(*) FROM auto_vehicle_claim WHERE link_id=%s",
            (links[12],),
        ).fetchone()[0] == 0
        db.execute(
            """UPDATE auto_proof_throttle
               SET window_started_at=now()-interval '61 minutes'
               WHERE actor_id=%s AND operation='ISSUE'""",
            (d["intake"],),
        )
    retry = _post(
        d, d["intake"], f"/api/v1/dev/auto/links/{links[12]}/issue-proof", {},
    )
    assert retry.status_code == 200
    with psycopg.connect(DSN) as db:
        assert db.execute(
            """SELECT attempts FROM auto_proof_throttle
               WHERE actor_id=%s AND operation='ISSUE'""",
            (d["intake"],),
        ).fetchone()[0] == 1
