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
