"""Smoke protocol checks; live execution remains the separate Compose job."""
from uuid import NAMESPACE_URL, uuid5

import httpx
import pytest

from scripts.check_local_stack_smoke import LOGIN, ORIGIN, RETIRED, VEHICLES, check


def run_smoke(*, bad_path=None, bad_status=500, replay_allowed=False,
              keep_cookie=False):
    expected = [
        ("GET", "/health/live", 200, {"status": "alive"}),
        ("GET", "/api/v1/meta", 200, {"service": "БоксРитм", "environment": "development"}),
        ("GET", RETIRED, 404, {"code": "NOT_FOUND"}),
        ("GET", VEHICLES, 401, {"code": "UNAUTHENTICATED"}),
        ("POST", "/api/v1/auth/login", 200, {"csrfToken": "synthetic-token"}),
        ("GET", "/api/v1/auth/me", 200,
         {"subjectId": str(uuid5(NAMESPACE_URL, "roboport-dev-only://" + LOGIN))}),
        ("GET", VEHICLES, 200, {"items": []}),
        ("POST", "/api/v1/auth/logout", 200, {"loggedOut": True}),
        ("GET", VEHICLES, 401, {"code": "UNAUTHENTICATED"}),
        ("GET", VEHICLES, 401, {"code": "UNAUTHENTICATED"}),
    ]
    calls = []

    def handle(request):
        method, path, status, body = expected[len(calls)]
        calls.append(path)
        assert request.method == method and request.url.path == path
        assert str(request.url).startswith(ORIGIN + "/")
        headers = {"Cache-Control": "no-store"}
        if path == "/api/v1/auth/login":
            assert request.headers["Origin"] == ORIGIN
            headers["Set-Cookie"] = "rp_session=synthetic; Path=/; Secure; HttpOnly"
        if path == "/api/v1/auth/logout":
            assert request.headers["Origin"] == ORIGIN
            assert request.headers["X-CSRF-Token"] == "synthetic-token"
            if not keep_cookie:
                headers["Set-Cookie"] = "rp_session=; Path=/; Max-Age=0; Secure; HttpOnly"
        if path == "/api/v1/auth/me" or (path == VEHICLES and status == 200):
            assert "rp_session=synthetic" in request.headers["Cookie"]
        if path == VEHICLES and status == 401:
            if len(calls) == len(expected):
                assert request.headers["Cookie"] == "rp_session=synthetic"
                if replay_allowed:
                    status, body = 200, {"items": []}
            else:
                assert "Cookie" not in request.headers
        if (path, status) == bad_path:
            status = bad_status
        return httpx.Response(status, json=body, headers=headers)

    with httpx.Client(base_url=ORIGIN, transport=httpx.MockTransport(handle),
                      trust_env=False, follow_redirects=False) as client:
        check(client, "disposable-only")
    assert calls == [item[1] for item in expected]


def test_current_smoke_protocol():
    run_smoke()


@pytest.mark.parametrize("path,status,wrong", [
    (RETIRED, 404, 200),
    (VEHICLES, 401, 200),
    ("/api/v1/auth/login", 200, 403),
    ("/api/v1/auth/me", 200, 503),
    (VEHICLES, 200, 503),
    ("/api/v1/auth/logout", 200, 403),
])
def test_smoke_fails_closed_on_wrong_http_status(path, status, wrong):
    with pytest.raises(RuntimeError, match="expected HTTP"):
        run_smoke(bad_path=(path, status), bad_status=wrong)


def test_deleted_cookie_does_not_hide_live_session():
    with pytest.raises(RuntimeError, match="revoked session replay: expected HTTP 401, got 200"):
        run_smoke(replay_allowed=True)


def test_logout_must_also_clear_cookie():
    with pytest.raises(RuntimeError, match="logout did not clear session cookie"):
        run_smoke(keep_cookie=True)
