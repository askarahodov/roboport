"""Synthetic direct-peer limiter cannot trust caller-controlled network headers."""
import hmac
import ipaddress
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient

from roboport_api.auto_peer_throttle import peer_fingerprint
from roboport_api.main import app

_KEY = "synthetic-test-hmac-key-" + "K" * 48
_URL = "/api/v1/dev/auto/links/00000000-0000-0000-0000-000000000001/issue-proof"


@pytest.mark.parametrize("peer", [
    None, (), ("127.0.0.1",), ("testclient", 1234),
    ("203.0.113.9", 1234), ("10.0.0.1", 3000),
    ("::ffff:127.0.0.1", 1234), ("::2", 3000),
    ("127.0.0.1", -1), ("127.0.0.1", 99999),
    ("127.0.0.1", True), ("127.0.0.1", "80"),
    ("127.0.0.1,203.0.113.4", 80),
])
def test_peer_fingerprint_denies_non_socket_and_non_loopback_addresses(peer):
    assert peer_fingerprint(peer, _KEY) is None


def test_hmac_canonicalizes_ip_without_persisting_plaintext():
    a = peer_fingerprint(("127.0.0.1", 49152), _KEY)
    b = peer_fingerprint(("127.0.0.1", 8080), _KEY)
    other = peer_fingerprint(("127.0.0.2", 8080), _KEY)
    ipv6 = peer_fingerprint(("::1", 8080), _KEY)
    assert a and a == b and a != other and a != ipv6
    assert len(a) == 64 and "127.0.0.1" not in a
    assert peer_fingerprint(("127.0.0.1", 1000), "short") is None
    assert peer_fingerprint(("127.0.0.1", 1000), "") is None
    assert peer_fingerprint(("127.0.0.1", 1000), _KEY + "changed") != a


def _flags(monkeypatch, *, flag="1", key=_KEY):
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTO_API", "1")
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTH_API", "1")
    monkeypatch.setenv("ROBOport_DATABASE_URL",
                       "postgresql://unused:unused@127.0.0.1:5432/roboport")
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTO_PEER_LIMIT", flag)
    if key is None:
        monkeypatch.delenv("ROBOport_SYNTHETIC_AUTO_PEER_HMAC_KEY", raising=False)
    else:
        monkeypatch.setenv("ROBOport_SYNTHETIC_AUTO_PEER_HMAC_KEY", key)


@pytest.mark.parametrize("flag,key,peer", [
    ("yes", _KEY, ("127.0.0.1", 22222)),
    ("1", None, ("127.0.0.1", 22222)),
    ("1", "weak", ("127.0.0.1", 22222)),
    ("1", _KEY, ("203.0.113.5", 22222)),
    ("1", _KEY, ("not-a-socket-ip", 22222)),
])
def test_invalid_flags_secret_or_peer_deny_before_db(monkeypatch, flag, key, peer):
    _flags(monkeypatch, flag=flag, key=key)

    def no_db(*args, **kwargs):
        raise AssertionError("Invalid peer/config must fail before database")

    monkeypatch.setattr(psycopg, "connect", no_db)
    response = TestClient(app, base_url="https://127.0.0.1", client=peer).post(_URL)
    assert response.status_code == 404
    assert response.json()["code"] == "NOT_FOUND"
    assert response.headers["cache-control"] == "no-store"
    assert _KEY not in response.text


def test_request_uses_socket_peer_not_spoofed_forwarding_metadata(monkeypatch):
    _flags(monkeypatch)
    def no_db(*args, **kwargs):
        raise AssertionError("XFF rejected before reaching persistence")
    monkeypatch.setattr(psycopg, "connect", no_db)
    response = TestClient(
        app, base_url="https://127.0.0.1", client=("127.0.0.1", 22222),
    ).post(_URL, headers={"X-Forwarded-For": "127.0.0.2"})
    assert response.status_code == 404 and response.json()["code"] == "NOT_FOUND"


def test_db_error_fails_closed_without_exposing_peer_or_hmac(monkeypatch):
    _flags(monkeypatch)
    import roboport_api.auto_peer_throttle as throttle

    def db_offline(*args):
        raise psycopg.OperationalError("synthetic database unavailable")

    monkeypatch.setattr(throttle, "consume_peer_request", db_offline)
    response = TestClient(
        app, base_url="https://127.0.0.1", client=("127.0.0.1", 22222),
    ).post(_URL)
    assert response.status_code == 503
    assert response.json()["code"] == "UNAVAILABLE"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["retry-after"] == "60"
    assert "127.0.0.1" not in response.text and _KEY not in response.text


def test_flag_default_disabled_and_non_auto_untouched(monkeypatch):
    monkeypatch.delenv("ROBOport_SYNTHETIC_AUTO_PEER_LIMIT", raising=False)
    assert TestClient(app).get("/api/v1/meta").status_code == 200
    assert TestClient(app).get("/health/live").status_code == 200


def test_packaged_uvicorn_still_disables_proxy_trust():
    dockerfile = (Path(__file__).resolve().parents[2] /
                  "apps/api/Dockerfile").read_text(encoding="utf-8")
    assert "--no-proxy-headers" in dockerfile
    assert "--forwarded-allow-ips=*" not in dockerfile
