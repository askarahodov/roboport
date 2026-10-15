"""HTTP cookie serialization policy for future auth routes."""
import pytest
from fastapi import Response
from roboport_api.session_cookie import set_session_cookie, clear_session_cookie


def test_session_cookie_has_secure_server_only_attributes():
    response=Response()
    set_session_cookie(response,"synthetic-token")
    raw=response.headers["set-cookie"]
    for item in ("HttpOnly", "Secure", "SameSite=lax", "Max-Age=43200", "Path=/"):
        assert item.lower() in raw.lower()
    assert raw.startswith("rp_session=synthetic-token;")


def test_plain_http_cookie_issue_is_explicitly_rejected():
    with pytest.raises(ValueError):
        set_session_cookie(Response(),"synthetic-token",tls=False)


def test_logout_cookie_clears_same_name_and_path():
    response=Response()
    clear_session_cookie(response)
    assert "rp_session=" in response.headers["set-cookie"]
    assert "Max-Age=0" in response.headers["set-cookie"]
