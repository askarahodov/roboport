"""PG17 HTTP regression for closed automotive company staff management."""
from uuid import uuid4

import psycopg
from fastapi.testclient import TestClient

from roboport_api.main import app
from test_pg_auto_vehicle_claim_http import _scenario
from test_pg_baseline import DSN


def test_dev_staff_http_requires_admin_tenant_session_and_csrf(monkeypatch):
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTO_API", "1")
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTH_API", "1")
    monkeypatch.setenv("ROBOport_DATABASE_URL", DSN)
    d = _scenario()
    org_a, org_b, loc = uuid4(), uuid4(), uuid4()
    with psycopg.connect(DSN) as db:
        for oid in (org_a, org_b):
            db.execute("INSERT INTO auto_organization(id,code,display_name) VALUES(%s,%s,'Synthetic')",
                       (oid, "ORG_" + oid.hex[:10].upper()))
        db.execute("INSERT INTO auto_service_location(id,code,organization_id) VALUES(%s,%s,%s)",
                   (loc, "LOC_" + loc.hex[:10].upper(), org_a))
        # Controlled synthetic fixture only; there is no bootstrap endpoint.
        db.execute("""INSERT INTO auto_organization_role_grant
            (id,organization_id,user_id,role_code) VALUES(%s,%s,%s,'ADMIN')""",
                   (uuid4(), org_a, d["manager"]))
    client = TestClient(app, base_url="https://127.0.0.1")
    url = f"/api/v1/dev/auto/organizations/{org_a}/staff"
    payload = {"userId": str(d["intake"]), "role": "INTAKE", "locationId": str(loc)}
    assert client.post(url, json=payload).status_code == 403
    client.cookies.set("rp_session", d["tokens"][d["manager"]])
    assert client.post(url, json=payload).status_code == 403
    headers = {"Origin": "https://127.0.0.1",
               "X-CSRF-Token": d["csrf"][d["manager"]]}
    wrong = f"/api/v1/dev/auto/organizations/{org_b}/staff"
    assert client.post(wrong, json=payload, headers=headers).status_code == 403
    assert client.post(url, json={**payload, "role": "ADMIN"},
                       headers=headers).status_code == 403
    # GET also requires a live ADMIN of this organization, without CSRF mutation token.
    assert client.get(wrong).status_code == 403
    listed = client.get(url)
    assert listed.status_code == 200, listed.text
    assert listed.json() == {"items": []}
    created = client.post(url, json=payload, headers=headers)
    assert created.status_code == 201, created.text
    ident = created.json()["grantId"]
    listed = client.get(url)
    assert listed.status_code == 200
    assert listed.json()["items"] == [{"grantId": ident, "userId": str(d["intake"]),
        "role": "INTAKE", "locationId": str(loc)}]
    assert client.post(url, json=payload, headers=headers).status_code == 409
    assert client.post(f"{wrong}/{ident}/revoke", headers=headers).status_code == 403
    revoked = client.post(f"{url}/{ident}/revoke", headers=headers)
    assert revoked.status_code == 200, revoked.text
    assert revoked.json()["status"] == "REVOKED"
    assert client.post(f"{url}/{ident}/revoke", headers=headers).json()["status"] == "ALREADY_REVOKED"
    assert client.get(url).json() == {"items": []}
    # Audit must retain both events after revoke, but never expose tenant B.
    audit = client.get(f"{url}/audit")
    assert audit.status_code == 200, audit.text
    assert audit.headers["cache-control"] == "no-store"
    events = audit.json()["items"]
    assert len(events) == 2
    assert {event["action"] for event in events} == {"GRANTED", "REVOKED"}
    assert {event["actorId"] for event in events} == {str(d["manager"])}
    assert {event["grantId"] for event in events} == {ident}
    assert {event["userId"] for event in events} == {str(d["intake"])}
    assert {event["role"] for event in events} == {"INTAKE"}
    assert client.get(f"{url}/audit?limit=1").status_code == 200
    assert len(client.get(f"{url}/audit?limit=1").json()["items"]) == 1
    for invalid in (0, 101, -1):
        assert client.get(f"{url}/audit?limit={invalid}").status_code == 422
    assert client.get(f"{wrong}/audit").status_code == 403
    with psycopg.connect(DSN) as db:
        assert db.execute("""SELECT actor_id,action FROM auto_org_staff_action
            WHERE grant_id=%s ORDER BY created_at,id""", (ident,)).fetchall() == [
            (d["manager"], "GRANTED"), (d["manager"], "REVOKED")
        ]


def test_dev_staff_http_is_disabled_without_flag(monkeypatch):
    monkeypatch.delenv("ROBOport_SYNTHETIC_AUTO_API", raising=False)
    client = TestClient(app, base_url="https://127.0.0.1")
    path = f"/api/v1/dev/auto/organizations/{uuid4()}/staff"
    assert client.post(path, json={"userId": str(uuid4()), "role": "INTAKE"}).status_code == 404
    assert client.get(f"{path}/audit").status_code == 404
