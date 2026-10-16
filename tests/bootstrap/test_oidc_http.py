"""Opt-in OIDC must never trust input identities or issue sessions by default."""
from fastapi.testclient import TestClient
from roboport_api.main import app
from roboport_api.oidc_http import _sign, _read, _config
from roboport_api.oidc import OIDCRejected
import pytest

def test_flow_is_disabled_without_explicit_env():
    client=TestClient(app,base_url="https://127.0.0.1")
    assert client.get("/api/v1/dev/oidc/start").status_code==403
    assert client.get("/api/v1/dev/oidc/callback?code=fake&state=fake").status_code==403

def test_signed_state_integrity_and_short_ttl(monkeypatch):
    import time
    key="synthetic-test-key-with-at-least-32-characters"
    state={"verifier":"synthetic","nonce":"synthetic-nonce","state":"synthetic-state","at":int(time.time())}
    good=_sign(state,key)
    assert _read(good,key)==state
    with pytest.raises(OIDCRejected):
        _read(good[:-2]+"ab",key)
    with pytest.raises(OIDCRejected):
        _read(good,"different-secret-at-least-32-characters")
    expired=_sign({**state,"at":int(time.time())-301},key)
    with pytest.raises(OIDCRejected):
        _read(expired,key)

def test_fake_client_headers_never_create_oidc_session():
    client=TestClient(app,base_url="https://127.0.0.1")
    for headers in ({"X-User-ID":"fake"},{"Authorization":"Bearer fake"},{"X-OIDC-Subject":"fake"}):
        assert client.get("/api/v1/auth/me",headers=headers).status_code==401
