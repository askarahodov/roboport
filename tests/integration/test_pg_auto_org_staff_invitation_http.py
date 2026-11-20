"""PG17: one-time organization staff invitations, acceptance, revocation and audit."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import hashlib
from uuid import uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient

from roboport_api.main import app
from test_pg_auto_vehicle_claim_http import _scenario
from test_pg_baseline import DSN


def _setup(monkeypatch):
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTO_API", "1")
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTH_API", "1")
    monkeypatch.setenv("ROBOport_DATABASE_URL", DSN)
    data = _scenario()
    org_a, org_b, location = uuid4(), uuid4(), uuid4()
    with psycopg.connect(DSN) as db:
        for org in (org_a, org_b):
            db.execute(
                "INSERT INTO auto_organization(id,code,display_name) VALUES(%s,%s,'Synthetic')",
                (org, "ORG_" + org.hex[:10].upper()),
            )
        db.execute(
            "INSERT INTO auto_service_location(id,code,organization_id) VALUES(%s,%s,%s)",
            (location, "LOC_" + location.hex[:10].upper(), org_a),
        )
        db.execute(
            """INSERT INTO auto_organization_role_grant
               (id,organization_id,user_id,role_code) VALUES(%s,%s,%s,'ADMIN')""",
            (uuid4(), org_a, data["manager"]),
        )
        logins = dict(db.execute(
            "SELECT id,normalized_login FROM app_user WHERE id=ANY(%s)",
            ([data["intake"], data["other"]],),
        ).fetchall())
    return data, org_a, org_b, location, logins


def _client(data, user):
    client = TestClient(app, base_url="https://127.0.0.1")
    client.cookies.set("rp_session", data["tokens"][user])
    return client


def _headers(data, user):
    return {
        "Origin": "https://127.0.0.1",
        "X-CSRF-Token": data["csrf"][user],
    }


def test_invitation_is_bound_to_authenticated_account_and_one_time(monkeypatch):
    data, org_a, org_b, location, logins = _setup(monkeypatch)
    admin = _client(data, data["manager"])
    target = _client(data, data["intake"])
    wrong_user = _client(data, data["other"])
    url = f"/api/v1/dev/auto/organizations/{org_a}/staff-invitations"
    payload = {
        "targetLogin": logins[data["intake"]],
        "role": "CASHIER",
        "locationId": str(location),
        "expiresInDays": 7,
    }

    # Session and CSRF are both required; ADMIN is never assignable by invite.
    assert TestClient(app, base_url="https://127.0.0.1").post(
        url, json=payload, headers=_headers(data, data["manager"])
    ).status_code == 403
    assert admin.post(url, json=payload).status_code == 403
    assert admin.post(url, json={**payload, "role": "ADMIN"},
                      headers=_headers(data, data["manager"])).status_code == 422

    created = admin.post(url, json=payload, headers=_headers(data, data["manager"]))
    assert created.status_code == 201, created.text
    assert created.headers["cache-control"] == "no-store"
    invite = created.json()
    secret = invite["token"]
    assert len(secret) == 43
    assert invite["targetLogin"] == logins[data["intake"]]
    assert invite["role"] == "CASHIER"
    assert admin.post(url, json=payload,
                      headers=_headers(data, data["manager"])).status_code == 409
    direct_grant = admin.post(
        f"/api/v1/dev/auto/organizations/{org_a}/staff",
        json={"userId":str(data["intake"]),"role":"CASHIER","locationId":str(location)},
        headers=_headers(data, data["manager"]),
    )
    assert direct_grant.status_code == 409

    # The plaintext secret is not stored or returned by list/audit endpoints.
    with psycopg.connect(DSN) as db:
        token_hash = db.execute(
            "SELECT token_hash FROM auto_org_staff_invitation WHERE id=%s",
            (invite["invitationId"],),
        ).fetchone()[0].strip()
    assert token_hash == hashlib.sha256(secret.encode("ascii")).hexdigest()
    assert token_hash != secret
    listed = admin.get(url)
    assert listed.status_code == 200
    assert len(listed.json()["items"]) == 1
    assert listed.json()["items"][0]["status"] == "PENDING"
    assert "token" not in listed.json()["items"][0]
    assert admin.get(
        f"/api/v1/dev/auto/organizations/{org_b}/staff-invitations"
    ).status_code == 403

    # A different authenticated account cannot consume the token.
    wrong = wrong_user.post(
        "/api/v1/dev/auto/staff-invitations/accept",
        json={"token": secret}, headers=_headers(data, data["other"]),
    )
    assert wrong.status_code == 403

    accepted = target.post(
        "/api/v1/dev/auto/staff-invitations/accept",
        json={"token": secret}, headers=_headers(data, data["intake"]),
    )
    assert accepted.status_code == 201, accepted.text
    assert accepted.json()["status"] == "ACCEPTED"
    assert accepted.json()["role"] == "CASHIER"
    duplicate_after_accept = admin.post(url, json=payload,
        headers=_headers(data, data["manager"]))
    assert duplicate_after_accept.status_code == 409
    replay = target.post(
        "/api/v1/dev/auto/staff-invitations/accept",
        json={"token": secret}, headers=_headers(data, data["intake"]),
    )
    assert replay.status_code == 409

    accepted_row = next(x for x in admin.get(url).json()["items"]
                        if x["invitationId"] == invite["invitationId"])
    assert accepted_row["status"] == "ACCEPTED"
    assert accepted_row["grantId"] == accepted.json()["grantId"]
    assert "token" not in accepted_row

    audit = admin.get(
        f"/api/v1/dev/auto/organizations/{org_a}/staff-invitations/audit"
    )
    assert audit.status_code == 200
    events = [x for x in audit.json()["items"]
              if x["invitationId"] == invite["invitationId"]]
    assert {x["action"] for x in events} == {"CREATED", "ACCEPTED"}
    assert {x["actorId"] for x in events} == {
        str(data["manager"]), str(data["intake"])
    }

    with psycopg.connect(DSN) as db:
        grant_actor = db.execute(
            """SELECT actor_id FROM auto_org_staff_action
               WHERE grant_id=%s AND action='GRANTED'""",
            (accepted.json()["grantId"],),
        ).fetchone()
        assert grant_actor == (data["manager"],)
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute(
                    "UPDATE auto_org_staff_invitation_event SET action='REVOKED' WHERE invitation_id=%s",
                    (invite["invitationId"],),
                )
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute(
                    "DELETE FROM auto_org_staff_invitation WHERE id=%s",
                    (invite["invitationId"],),
                )


def test_invitation_revoke_expiry_and_branch_retirement_blocker(monkeypatch):
    data, org_a, org_b, location, logins = _setup(monkeypatch)
    admin = _client(data, data["manager"])
    target = _client(data, data["other"])
    base = f"/api/v1/dev/auto/organizations/{org_a}/staff-invitations"
    headers = _headers(data, data["manager"])

    # Pending branch-scoped invitations are dependencies: a branch cannot be
    # retired while it still has an outstanding employee invitation.
    created = admin.post(base, json={
        "targetLogin": logins[data["other"]], "role": "WAREHOUSE",
        "locationId": str(location), "expiresInDays": 7,
    }, headers=headers)
    assert created.status_code == 201, created.text
    invitation = created.json()
    disable = f"/api/v1/dev/auto/organizations/{org_a}/locations/{location}/disable"
    blocked = admin.post(disable, headers=headers)
    assert blocked.status_code == 409

    revoked = admin.post(
        f"{base}/{invitation['invitationId']}/revoke", headers=headers
    )
    assert revoked.status_code == 200
    assert revoked.json()["status"] == "REVOKED"
    rejected = target.post(
        "/api/v1/dev/auto/staff-invitations/accept",
        json={"token": invitation["token"]},
        headers=_headers(data, data["other"]),
    )
    assert rejected.status_code == 409

    # Once the invitation is revoked, the otherwise empty branch can be retired.
    disabled = admin.post(disable, headers=headers)
    assert disabled.status_code == 200
    assert disabled.json()["status"] == "DISABLED"

    # Expired tokens transition once to EXPIRED and can never grant authority.
    expired_token = "Z" * 43
    expired_id = uuid4()
    old = datetime.now(timezone.utc) - timedelta(days=2)
    with psycopg.connect(DSN) as db:
        db.execute(
            """INSERT INTO auto_org_staff_invitation
               (id,organization_id,target_user_id,target_login,role_code,token_hash,
                created_by,created_at,expires_at)
               VALUES(%s,%s,%s,%s,'INTAKE',%s,%s,%s,%s)""",
            (expired_id, org_a, data["other"], logins[data["other"]],
             hashlib.sha256(expired_token.encode("ascii")).hexdigest(),
             data["manager"], old, old + timedelta(days=1)),
        )
        db.execute(
            """INSERT INTO auto_org_staff_invitation_event
               (id,invitation_id,organization_id,actor_id,action)
               VALUES(%s,%s,%s,%s,'CREATED')""",
            (uuid4(), expired_id, org_a, data["manager"]),
        )
    expired = target.post(
        "/api/v1/dev/auto/staff-invitations/accept",
        json={"token": expired_token},
        headers=_headers(data, data["other"]),
    )
    assert expired.status_code == 410
    with psycopg.connect(DSN) as db:
        assert db.execute(
            "SELECT status FROM auto_org_staff_invitation WHERE id=%s",
            (expired_id,),
        ).fetchone() == ("EXPIRED",)
        assert db.execute(
            """SELECT action FROM auto_org_staff_invitation_event
               WHERE invitation_id=%s ORDER BY created_at""",
            (expired_id,),
        ).fetchall() == [("CREATED",), ("EXPIRED",)]


def test_invitation_is_tenant_scoped_and_disabled_without_dev_flag(monkeypatch):
    data, org_a, org_b, location, logins = _setup(monkeypatch)
    admin = _client(data, data["manager"])
    path = f"/api/v1/dev/auto/organizations/{org_b}/staff-invitations"
    payload = {"targetLogin": logins[data["intake"]], "role": "INTAKE"}
    assert admin.post(path, json=payload,
                      headers=_headers(data, data["manager"])).status_code == 403
    monkeypatch.delenv("ROBOport_SYNTHETIC_AUTO_API", raising=False)
    disabled = admin.post(
        f"/api/v1/dev/auto/organizations/{org_a}/staff-invitations",
        json=payload, headers=_headers(data, data["manager"]),
    )
    assert disabled.status_code == 404


def test_concurrent_acceptance_is_exactly_once(monkeypatch):
    data, org_a, _org_b, location, logins = _setup(monkeypatch)
    admin = _client(data, data["manager"])
    base = f"/api/v1/dev/auto/organizations/{org_a}/staff-invitations"
    created = admin.post(base, json={
        "targetLogin": logins[data["intake"]], "role": "MECHANIC",
        "locationId": str(location), "expiresInDays": 7,
    }, headers=_headers(data, data["manager"]))
    assert created.status_code == 201, created.text
    secret = created.json()["token"]

    def accept_once(_):
        client = _client(data, data["intake"])
        try:
            response = client.post(
                "/api/v1/dev/auto/staff-invitations/accept",
                json={"token": secret}, headers=_headers(data, data["intake"]),
            )
            return response.status_code
        finally:
            client.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(accept_once, range(2)))
    assert sorted(results) == [201, 409]
    with psycopg.connect(DSN) as db:
        assert db.execute(
            """SELECT count(*) FROM auto_organization_role_grant
               WHERE organization_id=%s AND user_id=%s AND role_code='MECHANIC'
                 AND location_id=%s AND revoked_at IS NULL""",
            (org_a, data["intake"], location),
        ).fetchone() == (1,)
        assert db.execute(
            """SELECT action FROM auto_org_staff_invitation_event
               WHERE invitation_id=%s ORDER BY action""",
            (created.json()["invitationId"],),
        ).fetchall() == [("ACCEPTED",), ("CREATED",)]


def test_invitation_cannot_be_accepted_after_issuer_admin_is_revoked(monkeypatch):
    data, org_a, _org_b, _location, logins = _setup(monkeypatch)
    admin = _client(data, data["manager"])
    target = _client(data, data["intake"])
    created = admin.post(
        f"/api/v1/dev/auto/organizations/{org_a}/staff-invitations",
        json={"targetLogin":logins[data["intake"]],"role":"INTAKE"},
        headers=_headers(data, data["manager"]),
    )
    assert created.status_code == 201, created.text
    with psycopg.connect(DSN) as db:
        db.execute(
            """UPDATE auto_organization_role_grant SET revoked_at=now()
               WHERE organization_id=%s AND user_id=%s AND role_code='ADMIN'
                 AND location_id IS NULL AND revoked_at IS NULL""",
            (org_a, data["manager"]),
        )
    response = target.post(
        "/api/v1/dev/auto/staff-invitations/accept",
        json={"token":created.json()["token"]},
        headers=_headers(data, data["intake"]),
    )
    assert response.status_code == 403
    with psycopg.connect(DSN) as db:
        assert db.execute(
            "SELECT status FROM auto_org_staff_invitation WHERE id=%s",
            (created.json()["invitationId"],),
        ).fetchone() == ("PENDING",)
        assert db.execute(
            """SELECT count(*) FROM auto_organization_role_grant
               WHERE organization_id=%s AND user_id=%s AND role_code='INTAKE'
                 AND revoked_at IS NULL""",
            (org_a, data["intake"]),
        ).fetchone() == (0,)
