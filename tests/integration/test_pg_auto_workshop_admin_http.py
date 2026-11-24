"""PG17 HTTP acceptance for branch, bay and employee-shift administration."""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import psycopg
from fastapi.testclient import TestClient

from roboport_api.main import app
from test_pg_auto_vehicle_claim_http import _scenario
from test_pg_baseline import DSN


def test_admin_manages_branch_bays_shifts_and_immutable_audit(monkeypatch):
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTO_API", "1")
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTH_API", "1")
    monkeypatch.setenv("ROBOport_DATABASE_URL", DSN)
    d = _scenario()
    organization, other_organization = uuid4(), uuid4()
    branch_code = "BR_" + uuid4().hex[:8].upper()
    bay_code = "BAY_" + uuid4().hex[:8].upper()
    now = datetime.now(timezone.utc)
    starts = (now + timedelta(days=4)).replace(minute=0, second=0, microsecond=0)
    ends = starts + timedelta(hours=8)
    booking_starts = starts + timedelta(hours=1)
    booking_ends = booking_starts + timedelta(hours=1)

    with psycopg.connect(DSN) as db:
        for organization_id in (organization, other_organization):
            db.execute(
                "INSERT INTO auto_organization(id,code,display_name) VALUES(%s,%s,'Synthetic')",
                (organization_id, "ORG_" + organization_id.hex[:10].upper()),
            )
        db.execute(
            """INSERT INTO auto_organization_role_grant
               (id,organization_id,user_id,role_code)
               VALUES(%s,%s,%s,'ADMIN')""",
            (uuid4(), organization, d["manager"]),
        )

    client = TestClient(app, base_url="https://127.0.0.1")
    client.cookies.set("rp_session", d["tokens"][d["manager"]])
    headers = {
        "Origin": "https://127.0.0.1",
        "X-CSRF-Token": d["csrf"][d["manager"]],
    }
    root = f"/api/v1/dev/auto/organizations/{organization}"
    other_root = f"/api/v1/dev/auto/organizations/{other_organization}"

    # The new admin API is tenant-bound and cannot be used without a live ADMIN.
    assert client.get(other_root + "/workshop").status_code == 403
    assert client.get(root + "/workshop").status_code == 200
    assert client.get(root + "/workshop").json()["syntheticOnly"] is True

    invalid = client.post(
        root + "/locations", json={"code": branch_code, "timeZone": "Mars/Olympus"},
        headers=headers,
    )
    assert invalid.status_code == 422, invalid.text
    # ZoneInfo and PostgreSQL both know aliases such as CET, but the
    # existing location guard intentionally accepts only UTC or IANA paths.
    alias = client.post(
        root + "/locations", json={"code": branch_code, "timeZone": "CET"},
        headers=headers,
    )
    assert alias.status_code == 422, alias.text
    missing_key = client.post(
        root + "/locations",
        json={"code": branch_code, "timeZone": "Europe/Berlin"},
        headers=headers,
    )
    assert missing_key.status_code == 422, missing_key.text

    branch_headers = {**headers, "Idempotency-Key": "branch-create-key-0001"}
    created = client.post(
        root + "/locations",
        json={"code": branch_code, "timeZone": "Europe/Berlin"},
        headers=branch_headers,
    )
    assert created.status_code == 201, created.text
    location = created.json()["locationId"]
    replayed_branch = client.post(
        root + "/locations",
        json={"code": branch_code, "timeZone": "Europe/Berlin"},
        headers=branch_headers,
    )
    assert replayed_branch.status_code == 201
    assert replayed_branch.json()["locationId"] == location
    changed_payload = client.post(
        root + "/locations",
        json={"code": "BR_OTHER_" + uuid4().hex[:6].upper(), "timeZone": "Europe/Berlin"},
        headers=branch_headers,
    )
    assert changed_payload.status_code == 409
    duplicate_branch = client.post(
        root + "/locations",
        json={"code": branch_code, "timeZone": "Europe/Berlin"},
        headers={**headers, "Idempotency-Key": "branch-create-key-0002"},
    )
    assert duplicate_branch.status_code == 409

    branch_list = client.get(root + "/workshop")
    assert branch_list.status_code == 200
    assert any(x["locationId"] == location and x["timeZone"] == "Europe/Berlin"
               for x in branch_list.json()["locations"])

    # A bay is created only under an active branch of this organization.
    bay_headers = {**headers, "Idempotency-Key": "bay-create-key-0001"}
    bay_result = client.post(
        f"{root}/locations/{location}/bays",
        json={"code": bay_code}, headers=bay_headers,
    )
    assert bay_result.status_code == 201, bay_result.text
    bay = bay_result.json()["bayId"]
    replayed_bay = client.post(
        f"{root}/locations/{location}/bays",
        json={"code": bay_code}, headers=bay_headers,
    )
    assert replayed_bay.status_code == 201
    assert replayed_bay.json()["bayId"] == bay
    assert client.post(
        f"{root}/locations/{location}/bays",
        json={"code": bay_code},
        headers={**headers, "Idempotency-Key": "bay-create-key-0002"},
    ).status_code == 409
    assert client.post(
        f"{other_root}/locations/{location}/bays",
        json={"code": "BAY_FOREIGN"},
        headers={**headers, "Idempotency-Key": "bay-foreign-key-0001"},
    ).status_code == 403

    # Only a user with an active staff grant in this organization/branch can
    # receive a shift. A legacy global role alone is not organization authority.
    shift_payload = {
        "userId": str(d["intake"]),
        "startsAt": starts.isoformat(),
        "endsAt": ends.isoformat(),
    }
    shift_headers = {**headers, "Idempotency-Key": "shift-create-key-0001"}
    long_shift = client.post(
        f"{root}/locations/{location}/shifts",
        json={**shift_payload, "endsAt": (starts + timedelta(hours=25)).isoformat()},
        headers={**headers, "Idempotency-Key": "shift-too-long-key-0001"},
    )
    assert long_shift.status_code == 422, long_shift.text
    denied_shift = client.post(
        f"{root}/locations/{location}/shifts",
        json=shift_payload, headers=shift_headers,
    )
    assert denied_shift.status_code == 403, denied_shift.text
    with psycopg.connect(DSN) as db:
        db.execute(
            """INSERT INTO auto_organization_role_grant
               (id,organization_id,location_id,user_id,role_code)
               VALUES(%s,%s,%s,%s,'MECHANIC')""",
            (uuid4(), organization, location, d["intake"]),
        )
    shift_result = client.post(
        f"{root}/locations/{location}/shifts",
        json=shift_payload, headers=shift_headers,
    )
    assert shift_result.status_code == 201, shift_result.text
    shift = shift_result.json()["shiftId"]
    replayed_shift = client.post(
        f"{root}/locations/{location}/shifts",
        json=shift_payload, headers=shift_headers,
    )
    assert replayed_shift.status_code == 201
    assert replayed_shift.json()["shiftId"] == shift
    different_shift_payload = {**shift_payload, "endsAt": (ends + timedelta(hours=1)).isoformat()}
    assert client.post(
        f"{root}/locations/{location}/shifts",
        json=different_shift_payload, headers=shift_headers,
    ).status_code == 409
    overlap = client.post(
        f"{root}/locations/{location}/shifts",
        json=shift_payload,
        headers={**headers, "Idempotency-Key": "shift-overlap-key-0001"},
    )
    assert overlap.status_code == 409, overlap.text

    # A booked bay and active employee reservation prevent cancellation of the
    # shift and retirement of the bay until the appointment is reconciled.
    booking = uuid4()
    with psycopg.connect(DSN) as db:
        db.execute(
            """INSERT INTO auto_bay_booking
               (id,organization_id,location_id,bay_id,starts_at,ends_at)
               VALUES(%s,%s,%s,%s,%s,%s)""",
            (booking, organization, location, bay, booking_starts, booking_ends),
        )
        db.execute(
            """INSERT INTO auto_employee_reservation
               (id,booking_id,organization_id,location_id,employee_id,starts_at,ends_at)
               VALUES(%s,%s,%s,%s,%s,%s,%s)""",
            (uuid4(), booking, organization, location, d["intake"],
             booking_starts, booking_ends),
        )

    # Database guards must also reject direct writes that bypass the HTTP
    # service's friendly 409 response.
    with psycopg.connect(DSN) as db:
        for sql, params, label in (
            ("UPDATE auto_staff_shift SET canceled_at=now() WHERE id=%s", (shift,), "shift"),
            ("UPDATE auto_work_bay SET disabled_at=now() WHERE id=%s", (bay,), "bay"),
        ):
            try:
                with db.transaction():
                    db.execute(sql, params)
            except psycopg.errors.RaiseException:
                pass
            else:
                raise AssertionError(f"database guard accepted unsafe {label} change")

    cancel_blocked = client.post(
        f"{root}/locations/{location}/shifts/{shift}/cancel", headers=headers,
    )
    assert cancel_blocked.status_code == 409, cancel_blocked.text
    bay_blocked = client.post(
        f"{root}/locations/{location}/bays/{bay}/disable", headers=headers,
    )
    assert bay_blocked.status_code == 409, bay_blocked.text
    location_blocked = client.post(
        f"{root}/locations/{location}/disable", headers=headers,
    )
    assert location_blocked.status_code == 409, location_blocked.text

    with psycopg.connect(DSN) as db:
        db.execute("UPDATE auto_bay_booking SET canceled_at=now() WHERE id=%s", (booking,))

    canceled = client.post(
        f"{root}/locations/{location}/shifts/{shift}/cancel", headers=headers,
    )
    assert canceled.status_code == 200, canceled.text
    assert canceled.json()["status"] == "CANCELED"
    replay = client.post(
        f"{root}/locations/{location}/shifts/{shift}/cancel", headers=headers,
    )
    assert replay.status_code == 200
    assert replay.json()["status"] == "ALREADY_CANCELED"

    # A receipt never bypasses the current staff grant check.
    with psycopg.connect(DSN) as db:
        db.execute(
            """UPDATE auto_organization_role_grant SET revoked_at=now()
               WHERE organization_id=%s AND location_id=%s
                 AND user_id=%s AND role_code='MECHANIC' AND revoked_at IS NULL""",
            (organization, location, d["intake"]),
        )
    revoked_replay = client.post(
        f"{root}/locations/{location}/shifts",
        json=shift_payload, headers=shift_headers,
    )
    assert revoked_replay.status_code == 403, revoked_replay.text

    disabled_bay = client.post(
        f"{root}/locations/{location}/bays/{bay}/disable", headers=headers,
    )
    assert disabled_bay.status_code == 200, disabled_bay.text
    assert disabled_bay.json()["status"] == "DISABLED"

    # The branch grant was revoked above; branch deactivation can now proceed.
    disabled_location = client.post(
        f"{root}/locations/{location}/disable", headers=headers,
    )
    assert disabled_location.status_code == 200, disabled_location.text
    assert disabled_location.json()["status"] == "DISABLED"
    assert client.post(
        f"{root}/locations/{location}/bays",
        json={"code": "BAY_AFTER_DISABLE"},
        headers={**headers, "Idempotency-Key": "bay-after-disable-0001"},
    ).status_code == 403

    data = client.get(root + "/workshop")
    assert data.status_code == 200
    assert next(x for x in data.json()["locations"] if x["locationId"] == location)["disabled"]
    assert next(x for x in data.json()["bays"] if x["bayId"] == bay)["disabled"]

    audit = client.get(root + "/workshop/audit?limit=100")
    assert audit.status_code == 200, audit.text
    assert audit.headers["cache-control"] == "no-store"
    actions = [x["action"] for x in audit.json()["items"]]
    assert set(actions) >= {
        "LOCATION_CREATED", "BAY_CREATED", "SHIFT_CREATED",
        "SHIFT_CANCELED", "BAY_DISABLED", "LOCATION_DISABLED",
    }
    # Idempotent replays must not create duplicate audit events.
    assert actions.count("LOCATION_CREATED") == 1
    assert actions.count("BAY_CREATED") == 1
    assert actions.count("SHIFT_CREATED") == 1
    assert all(x["actorId"] == str(d["manager"]) for x in audit.json()["items"])
    with psycopg.connect(DSN) as db:
        event = db.execute(
            """SELECT id FROM auto_workshop_admin_action
               WHERE organization_id=%s LIMIT 1""", (organization,),
        ).fetchone()[0]
        try:
            with db.transaction():
                db.execute(
                    "UPDATE auto_workshop_admin_action SET action='SHIFT_CREATED' WHERE id=%s",
                    (event,),
                )
        except psycopg.errors.RaiseException:
            pass
        else:
            raise AssertionError("workshop admin audit accepted UPDATE")
        try:
            with db.transaction():
                db.execute(
                    "DELETE FROM auto_workshop_admin_action WHERE id=%s", (event,),
                )
        except psycopg.errors.RaiseException:
            pass
        else:
            raise AssertionError("workshop admin audit accepted DELETE")
    with psycopg.connect(DSN) as db:
        db.execute(
            """UPDATE auto_organization_role_grant SET revoked_at=now()
               WHERE organization_id=%s AND user_id=%s
                 AND role_code='ADMIN' AND location_id IS NULL AND revoked_at IS NULL""",
            (organization, d["manager"]),
        )
    denied_replay = client.post(
        root + "/locations",
        json={"code": branch_code, "timeZone": "Europe/Berlin"},
        headers=branch_headers,
    )
    assert denied_replay.status_code == 403, denied_replay.text


def test_workshop_admin_api_is_disabled_without_synthetic_flag(monkeypatch):
    monkeypatch.delenv("ROBOport_SYNTHETIC_AUTO_API", raising=False)
    client = TestClient(app, base_url="https://127.0.0.1")
    path = f"/api/v1/dev/auto/organizations/{uuid4()}/workshop"
    assert client.get(path).status_code == 404
    assert client.post(path + "/locations", json={"code": "BRANCH_1",
        "timeZone": "Europe/Berlin"}).status_code == 404



def test_workshop_admin_can_reach_every_branch_bay_shift_and_audit_page(monkeypatch):
    from roboport_api.auto_workshop_admin import create_location, create_bay, create_shift
    from roboport_api.auto_org_staff import grant_staff

    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTO_API", "1")
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTH_API", "1")
    monkeypatch.setenv("ROBOport_DATABASE_URL", DSN)
    d = _scenario()
    org, other = uuid4(), uuid4()
    actor, employee = d["manager"], d["intake"]
    token = d["tokens"][actor]
    start = datetime.now(timezone.utc) + timedelta(days=10)
    with psycopg.connect(DSN) as db:
        for oid in (org, other):
            db.execute("INSERT INTO auto_organization(id,code,display_name) VALUES(%s,%s,'Synthetic')",
                       (oid,"ORG_"+oid.hex[:10].upper()))
            db.execute("""INSERT INTO auto_organization_role_grant
                       (id,organization_id,user_id,role_code) VALUES(%s,%s,%s,'ADMIN')""",
                       (uuid4(),oid,actor))
        grant_staff(db,token,org,employee,"MECHANIC")
        for i in range(102):
            loc = create_location(db,token,org,"BR_"+uuid4().hex[:12].upper(),"UTC",str(uuid4()))
            create_bay(db,token,org,loc,"BAY_1",str(uuid4()))
            begin = start+timedelta(days=i)
            create_shift(db,token,org,loc,employee,begin,begin+timedelta(hours=1),str(uuid4()))
    client=TestClient(app,base_url="https://127.0.0.1")
    client.cookies.set("rp_session",token)
    base=f"/api/v1/dev/auto/organizations/{org}/workshop"
    foreign=f"/api/v1/dev/auto/organizations/{other}/workshop"
    response=client.get(base)
    assert response.status_code==200,response.text
    initial=response.json()
    cursors={}
    for kind,key in (("locations","locationId"),("bays","bayId"),("shifts","shiftId")):
        assert initial[kind+"Truncated"] is True
        assert len(initial[kind])==100
        cursor=initial[kind+"NextCursor"]
        cursors[kind]=cursor
        params={"kind":kind,"cursor":cursor}
        tail=client.get(base+"/page",params=params)
        assert tail.status_code==200,tail.text
        assert tail.headers["cache-control"]=="no-store"
        assert len(tail.json()["items"])==2
        assert "nextCursor" not in tail.json()
        assert len({x[key] for x in initial[kind]+tail.json()["items"]})==102
        assert client.get(foreign+"/page",params=params).status_code==422
        assert client.get(base+"/page",params={**params,"cursor":cursor+"x"}).status_code==422
        assert client.get(base+"/page?kind="+kind+"&cursor="+cursor+"&cursor="+cursor).status_code==422
    assert client.get(base+"/page",params={"kind":"bays","cursor":cursors["locations"]}).status_code==422
    assert client.get(base+"/page",params={"kind":"locations"}).status_code==422
    history=client.get(base+"/audit").json()
    first_cursor=history["nextCursor"]
    events=history["items"]
    while "nextCursor" in history:
        history=client.get(base+"/audit",params={"cursor":history["nextCursor"]}).json()
        events+=history["items"]
    assert len(events)==306
    assert len({x["eventId"] for x in events})==306
    assert client.get(foreign+"/audit",params={"cursor":first_cursor}).status_code==422
    assert client.get(base+"/audit",params={"cursor":cursors["locations"]}).status_code==422
    with psycopg.connect(DSN) as db:
        db.execute("""UPDATE auto_organization_role_grant SET revoked_at=now()
                   WHERE organization_id=%s AND user_id=%s AND role_code='ADMIN'""",(org,actor))
    for kind,cursor in cursors.items():
        assert client.get(base+"/page",params={"kind":kind,"cursor":cursor}).status_code==403
    assert client.get(base+"/audit",params={"cursor":first_cursor}).status_code==403
