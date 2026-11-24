"""PG17: одноразовые приглашения сотрудников, принятие, отзыв и аудит."""
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

    # Требуются сессия и CSRF; роль ADMIN нельзя выдать через приглашение.
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

    # Секрет не хранится открытым текстом и не возвращается списком/аудитом.
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

    # Другая аутентифицированная учётная запись не может использовать этот токен.
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
    # Приглашение должно открывать портал без наследования старой глобальной роли.
    with psycopg.connect(DSN) as db:
        db.execute(
            "UPDATE user_role SET revoked_at=now() WHERE user_id=%s",
            (data["intake"],),
        )
    staff_access = target.get("/api/v1/dev/staff/access")
    assert staff_access.status_code == 200, staff_access.text
    assert staff_access.json()["roles"] == ["FINANCE"]
    assert staff_access.json()["legacyRoles"] == []
    assert staff_access.json()["organizationScopes"] == [{
        "organizationId": str(org_a),
        "locationId": str(location),
        "role": "CASHIER",
    }]
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

    # Ожидающее приглашение с областью филиала является зависимостью:
    # филиал нельзя отключить, пока приглашение не закрыто.
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

    # После отзыва приглашения пустой филиал можно отключить.
    disabled = admin.post(disable, headers=headers)
    assert disabled.status_code == 200
    assert disabled.json()["status"] == "DISABLED"

    # Истёкший токен однократно переходит в EXPIRED и больше не выдаёт полномочий.
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



def test_staff_admin_lists_complete_pages_with_scoped_cursors(monkeypatch):
    from roboport_api.auto_org_staff import grant_staff, create_staff_invitation

    data, org_a, org_b, location, logins = _setup(monkeypatch)
    actor = data["manager"]
    admin = _client(data, actor)
    # Настоящие назначения и приглашения в 102 филиалах, с равными
    # transaction timestamps для событий назначений. UUID разбивает tie.
    with psycopg.connect(DSN) as db:
        db.execute("""INSERT INTO auto_organization_role_grant
                   (id,organization_id,user_id,role_code) VALUES(%s,%s,%s,'ADMIN')""",
                   (uuid4(), org_b, actor))
        for index in range(102):
            loc = uuid4()
            db.execute("INSERT INTO auto_service_location(id,code,organization_id) VALUES(%s,%s,%s)",
                       (loc, "LOC_" + loc.hex[:10].upper(), org_a))
            grant_staff(db, data["tokens"][actor], org_a, data["intake"], "INTAKE", loc)
            create_staff_invitation(db, data["tokens"][actor], org_a,
                                    logins[data["other"]], "MECHANIC", loc)
    base = f"/api/v1/dev/auto/organizations/{org_a}"
    other = f"/api/v1/dev/auto/organizations/{org_b}"
    cursors = {}
    for suffix, key in (("/staff","grantId"),("/staff/audit","eventId"),
                        ("/staff-invitations","invitationId"),
                        ("/staff-invitations/audit","eventId")):
        url = base + suffix
        first = admin.get(url)
        assert first.status_code == 200, first.text
        assert first.headers["cache-control"] == "no-store"
        page = first.json()
        assert len(page["items"]) == 100
        cursor = page["nextCursor"]
        cursors[suffix] = cursor
        tail = admin.get(url, params={"cursor": cursor, "limit": 10})
        assert tail.status_code == 200, tail.text
        assert len(tail.json()["items"]) == 2
        assert "nextCursor" not in tail.json()
        assert len({row[key] for row in page["items"] + tail.json()["items"]}) == 102
        assert all("token" not in row and "tokenHash" not in row for row in page["items"])
        assert admin.get(other + suffix, params={"cursor": cursor}).status_code == 422
        assert admin.get(url, params={"cursor": cursor+"x"}).status_code == 422
        assert admin.get(url+"?cursor="+cursor+"&cursor="+cursor).status_code == 422
        assert admin.get(url+"?limit=2&limit=3").status_code == 422
        assert _client(data, data["intake"]).get(url, params={"cursor": cursor}).status_code == 403
    assert admin.get(base+"/staff/audit", params={"cursor":cursors["/staff"]}).status_code == 422
    # Вторая действующая сессия того же ADMIN не наследует подписанный курсор.
    from test_pg_auto_vehicle_claim_http import new_token, token_digest
    second_token,second_csrf=new_token(),new_token()
    now=datetime.now(timezone.utc)
    with psycopg.connect(DSN) as db:
        db.execute("""INSERT INTO user_session
            (id,user_id,token_hash,csrf_hash,created_at,idle_expires_at,absolute_expires_at)
            VALUES(%s,%s,%s,%s,%s,%s,%s)""",
            (uuid4(),actor,token_digest(second_token),token_digest(second_csrf),
             now,now+timedelta(minutes=30),now+timedelta(hours=4)))
    second=TestClient(app,base_url="https://127.0.0.1")
    second.cookies.set("rp_session",second_token)
    for suffix,cursor in cursors.items():
        assert second.get(base+suffix).status_code==200
        assert second.get(base+suffix,params={"cursor":cursor}).status_code==422
    # Отзыв права должен остановить любое чтение обеих сессий.
    assert admin.get(base+"/access-policies").status_code==200
    assert admin.get(base+"/workshop").status_code==200
    assert admin.get(base+"/workshop/audit").status_code==200
    with psycopg.connect(DSN) as db:
        db.execute("""UPDATE auto_organization_role_grant SET revoked_at=now()
                   WHERE organization_id=%s AND user_id=%s AND role_code='ADMIN'""", (org_a, actor))
    for reader in (admin,second):
        for suffix,cursor in cursors.items():
            assert reader.get(base+suffix).status_code==403
            assert reader.get(base+suffix,params={"cursor":cursor}).status_code==403
        for suffix in ("/access-policies","/workshop","/workshop/audit"):
            assert reader.get(base+suffix).status_code==403
