"""PG17: two independent offline approvals and one-time ADMIN bootstrap."""
from datetime import datetime, timezone
import hmac
from uuid import uuid4

import psycopg
import pytest

from roboport_api.auto_org_bootstrap import (
    BootstrapDenied, approval_message, bootstrap_first_admin,
)
from test_pg_auto_vehicle_claim_http import _scenario
from test_pg_baseline import DSN


def test_admin_bootstrap_requires_two_valid_keys_and_is_one_time():
    d = _scenario()
    org = uuid4()
    target = d["manager"]
    expiry = int(datetime.now(timezone.utc).timestamp()) + 300
    nonce = uuid4().hex
    keys = (b"a" * 32, b"b" * 32)
    msg = approval_message(org, target, expiry, nonce)
    signatures = tuple(hmac.digest(k, msg, "sha256").hex() for k in keys)
    with psycopg.connect(DSN) as db:
        db.execute(
            "INSERT INTO auto_organization(id,code,display_name) VALUES(%s,%s,'Synthetic')",
            (org, "ORG_" + org.hex[:10].upper()),
        )
    with psycopg.connect(DSN) as db:
        for candidate in (
            (signatures, (keys[0], keys[0])),
            ((signatures[0], "0" * 64), keys),
            (signatures, (keys[1], keys[0])),
        ):
            with pytest.raises(BootstrapDenied):
                bootstrap_first_admin(db, org, target, expiry, nonce, *candidate)
        with pytest.raises(BootstrapDenied):
            bootstrap_first_admin(db, org, target, expiry - 3600, nonce, signatures, keys)
        grant = bootstrap_first_admin(db, org, target, expiry, nonce, signatures, keys)
        with pytest.raises(BootstrapDenied):
            bootstrap_first_admin(db, org, target, expiry, nonce, signatures, keys)
        assert db.execute(
            "SELECT target_id FROM auto_org_bootstrap_receipt WHERE grant_id=%s",
            (grant,),
        ).fetchone() == (target,)
        with pytest.raises(psycopg.errors.RaiseException):
            with db.transaction():
                db.execute("DELETE FROM auto_org_bootstrap_receipt WHERE grant_id=%s", (grant,))


def test_admin_bootstrap_has_no_legacy_role_fallback():
    d = _scenario()
    org = uuid4()
    expiry = int(datetime.now(timezone.utc).timestamp()) + 300
    nonce = uuid4().hex
    keys = (b"a" * 32, b"b" * 32)
    msg = approval_message(org, d["manager"], expiry, nonce)
    signatures = tuple(hmac.digest(k, msg, "sha256").hex() for k in keys)
    with psycopg.connect(DSN) as db:
        with pytest.raises(BootstrapDenied):
            bootstrap_first_admin(db, org, d["manager"], expiry, nonce, signatures, keys)


def wait_for_blocked_backend(db, pid):
    """Наблюдаем реальное ожидание блокировки, не предполагаем его по sleep."""
    from time import monotonic,sleep
    until=monotonic()+5
    while monotonic()<until:
        if db.execute("SELECT pg_backend_pid()=ANY(pg_blocking_pids(%s))",
                      (pid,)).fetchone()[0]:
            return
        sleep(0.025)
    pytest.fail("expected PostgreSQL lock wait was not observed")


def test_bootstrap_approval_expires_while_waiting_for_organization():
    from concurrent.futures import ThreadPoolExecutor
    from queue import Queue

    d=_scenario()
    org=uuid4()
    target=d["manager"]
    keys=(b"a"*32,b"b"*32)
    with psycopg.connect(DSN) as db:
        db.execute("INSERT INTO auto_organization(id,code,display_name) VALUES(%s,%s,'Synthetic')",
                   (org,"ORG_"+org.hex[:10].upper()))
    ready=Queue()
    def attempt(expiry,nonce,signatures):
        with psycopg.connect(DSN) as db:
            db.execute("SET lock_timeout='15s'")
            ready.put(db.info.backend_pid)
            with pytest.raises(BootstrapDenied,match="expired while waiting"):
                bootstrap_first_admin(db,org,target,expiry,nonce,signatures,keys)

    with ThreadPoolExecutor(max_workers=1) as pool:
        with psycopg.connect(DSN) as lock:
            lock.execute("SELECT id FROM auto_organization WHERE id=%s FOR UPDATE",(org,))
            expiry=int(lock.execute("SELECT extract(epoch FROM clock_timestamp())").fetchone()[0])+5
            nonce=uuid4().hex
            msg=approval_message(org,target,expiry,nonce)
            signatures=tuple(hmac.digest(k,msg,"sha256").hex() for k in keys)
            future=pool.submit(attempt,expiry,nonce,signatures)
            wait_for_blocked_backend(lock,ready.get(timeout=5))
            lock.execute("SELECT pg_sleep(GREATEST(0,extract(epoch FROM to_timestamp(%s)-clock_timestamp())+0.15))",
                         (expiry,))
        future.result(timeout=15)
    with psycopg.connect(DSN) as db:
        assert db.execute("SELECT count(*) FROM auto_organization_role_grant WHERE organization_id=%s",
                          (org,)).fetchone()==(0,)
        assert db.execute("SELECT count(*) FROM auto_org_bootstrap_receipt WHERE organization_id=%s",
                          (org,)).fetchone()==(0,)
        expiry=int(datetime.now(timezone.utc).timestamp())+300
        nonce=uuid4().hex
        msg=approval_message(org,target,expiry,nonce)
        signatures=tuple(hmac.digest(k,msg,"sha256").hex() for k in keys)
        grant=bootstrap_first_admin(db,org,target,expiry,nonce,signatures,keys)
        assert db.execute("SELECT grant_id FROM auto_org_bootstrap_receipt WHERE organization_id=%s",
                          (org,)).fetchone()==(grant,)
