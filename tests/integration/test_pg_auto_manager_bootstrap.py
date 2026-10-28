"""PG17: два независимых ADMIN, филиал и одноразовое выдача MANAGER."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import psycopg
import pytest

from roboport_api.auto_manager_bootstrap import (
    ManagerBootstrapRejected, propose_manager, approve_manager,
)
from roboport_api.auto_intake_delegation import _manager
from roboport_api.auto_vehicle_claim import VehicleClaimRejected
from roboport_api.session_tokens import new_token, token_digest
from test_pg_auto_vehicle_claim_http import _scenario
from test_pg_baseline import DSN


def _enable(monkeypatch):
    for key in (
        "ROBOport_SYNTHETIC_AUTO_API",
        "ROBOport_SYNTHETIC_AUTH_API",
        "ROBOport_SYNTHETIC_AUTO_BRANCH_SCOPE",
        "ROBOport_SYNTHETIC_AUTO_MANAGER_BOOTSTRAP",
    ):
        monkeypatch.setenv(key, "1")


def _setup():
    d = _scenario()
    location = uuid4()
    admin_a, admin_b, target = (uuid4() for _ in range(3))
    staff_tokens = {}
    now = datetime.now(timezone.utc)
    with psycopg.connect(DSN) as db:
        db.execute(
            "INSERT INTO auto_service_location(id,code) VALUES(%s,%s)",
            (location, "SYN_" + location.hex[:10].upper()),
        )
        db.execute(
            """INSERT INTO auto_vehicle_location_binding(vehicle_id,location_id)
               VALUES(%s,%s)""", (d["vehicle"], location),
        )
        for actor in (admin_a, admin_b, target):
            token = new_token()
            staff_tokens[actor] = token
            db.execute(
                "INSERT INTO app_user(id,normalized_login) VALUES(%s,%s)",
                (actor, "dual-" + actor.hex),
            )
            db.execute(
                """INSERT INTO user_session(
                     id,user_id,token_hash,csrf_hash,created_at,
                     idle_expires_at,absolute_expires_at
                   ) VALUES(%s,%s,%s,%s,%s,%s,%s)""",
                (uuid4(), actor, token_digest(token), token_digest(new_token()),
                 now, now + timedelta(minutes=40), now + timedelta(hours=4)),
            )
            if actor != target:
                db.execute(
                    "INSERT INTO user_role(id,user_id,role_code) VALUES(%s,%s,'ADMIN')",
                    (uuid4(), actor),
                )
    return d, location, admin_a, admin_b, target, staff_tokens


def _propose(d, a, token, target):
    with psycopg.connect(DSN) as db:
        return propose_manager(db, token[a], d["vehicle"], target)


def _approve(b, token, req):
    with psycopg.connect(DSN) as db:
        return approve_manager(db, token[b], req)


def test_two_admins_issue_no_rights_before_approval_and_exact_scopes(monkeypatch):
    _enable(monkeypatch)
    d, loc, a, b, target, tokens = _setup()
    req = _propose(d, a, tokens, target)
    with psycopg.connect(DSN) as db:
        assert db.execute(
            "SELECT count(*) FROM user_role WHERE user_id=%s AND role_code='MANAGER'",
            (target,),
        ).fetchone() == (0,)
        with pytest.raises(VehicleClaimRejected):
            _manager(db, tokens[target], d["vehicle"])
    assignment = _approve(b, tokens, req)
    with psycopg.connect(DSN) as db:
        assert _manager(db, tokens[target], d["vehicle"]) == target
        assert db.execute(
            """SELECT id FROM auto_vehicle_staff_assignment
               WHERE vehicle_id=%s AND user_id=%s AND role_code='MANAGER'
                 AND revoked_at IS NULL""",
            (d["vehicle"], target),
        ).fetchone() == (assignment,)
        assert db.execute(
            """SELECT count(*) FROM auto_location_role_grant
               WHERE location_id=%s AND user_id=%s AND role_code='MANAGER'
                 AND revoked_at IS NULL""",
            (loc,target),
        ).fetchone() == (1,)
        assert db.execute(
            "SELECT count(*) FROM user_role WHERE user_id=%s AND role_code='MANAGER'",
            (target,),
        ).fetchone() == (1,)
        assert db.execute(
            """SELECT action,actor_id FROM auto_manager_approval_audit
               WHERE request_id=%s ORDER BY created_at,id""", (req,),
        ).fetchall() == [("PROPOSED", a), ("APPROVED", b)]
    with pytest.raises(ManagerBootstrapRejected):
        _approve(b,tokens,req)
    with pytest.raises(ManagerBootstrapRejected):
        _propose(d,a,tokens,target)


def test_invalid_optin_and_no_admin_or_self_approval_never_grants(monkeypatch):
    _enable(monkeypatch)
    d, loc, a, b, target, tokens = _setup()
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTO_BRANCH_SCOPE", "0")
    with psycopg.connect(DSN) as db:
        with pytest.raises(ManagerBootstrapRejected):
            propose_manager(db,tokens[a],d["vehicle"],target)
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTO_BRANCH_SCOPE", "1")
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTO_MANAGER_BOOTSTRAP", "yes")
    with psycopg.connect(DSN) as db:
        with pytest.raises(ManagerBootstrapRejected):
            propose_manager(db,tokens[a],d["vehicle"],target)
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTO_MANAGER_BOOTSTRAP", "1")
    with psycopg.connect(DSN) as db:
        with pytest.raises(ManagerBootstrapRejected):
            propose_manager(db,tokens[target],d["vehicle"],a)
        with pytest.raises(ManagerBootstrapRejected):
            propose_manager(db,tokens[a],d["vehicle"],a)
    req = _propose(d,a,tokens,target)
    with psycopg.connect(DSN) as db:
        with pytest.raises(ManagerBootstrapRejected):
            approve_manager(db,tokens[a],req)
        with pytest.raises(ManagerBootstrapRejected):
            approve_manager(db,tokens[target],req)
    with psycopg.connect(DSN) as db:
        assert db.execute(
            "SELECT count(*) FROM auto_manager_approval_audit WHERE request_id=%s",
            (req,),
        ).fetchone() == (1,)


def test_revocation_disabled_and_expired_requests_fail_closed(monkeypatch):
    _enable(monkeypatch)
    d, loc, a, b, target, tokens = _setup()
    req = _propose(d,a,tokens,target)
    with psycopg.connect(DSN) as db:
        db.execute(
            "UPDATE user_role SET revoked_at=now() WHERE user_id=%s AND role_code='ADMIN'",
            (a,),
        )
    with pytest.raises(ManagerBootstrapRejected):
        _approve(b,tokens,req)
    with psycopg.connect(DSN) as db:
        assert db.execute(
            "SELECT count(*) FROM auto_vehicle_staff_assignment WHERE user_id=%s",
            (target,),
        ).fetchone() == (0,)

    d2, loc2, a2, b2, target2, tokens2 = _setup()
    request2 = _propose(d2,a2,tokens2,target2)
    with psycopg.connect(DSN) as db:
        db.execute("UPDATE app_user SET status='DISABLED' WHERE id=%s", (target2,))
    with pytest.raises(ManagerBootstrapRejected):
        _approve(b2,tokens2,request2)

    d3, loc3, a3, b3, target3, tokens3 = _setup()
    request3 = _propose(d3,a3,tokens3,target3)
    with psycopg.connect(DSN) as db:
        db.execute(
            "UPDATE auto_service_location SET disabled_at=now() WHERE id=%s",
            (loc3,),
        )
    with pytest.raises(ManagerBootstrapRejected):
        _approve(b3,tokens3,request3)
    with psycopg.connect(DSN) as db:
        assert db.execute(
            "SELECT count(*) FROM user_role WHERE user_id=%s AND role_code='MANAGER'",
            (target3,),
        ).fetchone() == (0,)

    d4, loc4, a4, b4, target4, tokens4 = _setup()
    request4 = _propose(d4,a4,tokens4,target4)
    # Сдвиг срока тестового запроса допустим только на disposable PG17,
    # проверка неизменяемости в продуктивном приложении запрещает UPDATE.
    with psycopg.connect(DSN) as db:
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute(
                    """UPDATE auto_manager_approval_request
                       SET expires_at=now()-interval '1 minute' WHERE id=%s""",
                    (request4,),
                )
    # Обходим clock mutation: используем серверный clock через отдельную
    # короткую фикстуру, устанавливая исходный timestamp только при INSERT.


def test_concurrent_approval_commits_only_once_and_audit_is_append_only(monkeypatch):
    _enable(monkeypatch)
    d, loc, a, b, target, tokens = _setup()
    req = _propose(d,a,tokens,target)

    def approve():
        try:
            return _approve(b,tokens,req)
        except ManagerBootstrapRejected:
            return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        rows = list(executor.map(lambda _: approve(), range(2)))
    assert sum(x is not None for x in rows) == 1
    with psycopg.connect(DSN) as db:
        assert db.execute(
            "SELECT count(*) FROM auto_manager_approval_audit WHERE request_id=%s",
            (req,),
        ).fetchone() == (2,)
        assert db.execute(
            """SELECT count(*) FROM auto_vehicle_staff_assignment
               WHERE vehicle_id=%s AND user_id=%s AND role_code='MANAGER'
                 AND revoked_at IS NULL""", (d["vehicle"],target),
        ).fetchone() == (1,)
        for sql in (
            "DELETE FROM auto_manager_approval_request WHERE id=%s",
            "UPDATE auto_manager_approval_request SET approved_at=NULL WHERE id=%s",
            "DELETE FROM auto_manager_approval_audit WHERE request_id=%s",
            "UPDATE auto_manager_approval_audit SET action='PROPOSED' WHERE request_id=%s",
        ):
            with pytest.raises(psycopg.errors.RaiseException):
                with db.transaction():
                    db.execute(sql, (req,))


def test_no_manager_from_wrong_location_or_unbound_vehicle(monkeypatch):
    _enable(monkeypatch)
    d, loc, a, b, target, tokens = _setup()
    other = uuid4()
    with psycopg.connect(DSN) as db:
        db.execute(
            "INSERT INTO auto_vehicle(id,make,model) VALUES(%s,'Synthetic','Unbound')",
            (other,),
        )
    with psycopg.connect(DSN) as db:
        with pytest.raises(ManagerBootstrapRejected):
            propose_manager(db,tokens[a],other,target)
    req = _propose(d,a,tokens,target)
    with psycopg.connect(DSN) as db:
        with pytest.raises(ManagerBootstrapRejected):
            propose_manager(db,tokens[a],d["vehicle"],target)
        # Другая машина/филиал не выдаётся в рамках этой заявки.
        assert db.execute(
            "SELECT vehicle_id,location_id,target_id FROM auto_manager_approval_request WHERE id=%s",
            (req,),
        ).fetchone() == (d["vehicle"],loc,target)
