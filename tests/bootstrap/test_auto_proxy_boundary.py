"""Issue #159: synthetic auto must not trust request-supplied proxy metadata."""
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient

from roboport_api.auto_proxy_boundary import has_untrusted_proxy_headers
from roboport_api.main import app


@pytest.mark.parametrize("header", [
    "Forwarded", "X-Forwarded-For", "X-Forwarded-Proto",
    "X-Forwarded-Host", "X-Forwarded-Port", "X-Forwarded-Prefix",
    "X-Forwarded-Ssl", "X-Real-IP", "X-Original-Host",
    "X-Client-IP", "Client-IP", "True-Client-IP", "Front-End-Https",
    "X-Url-Scheme", "X-Forwarded-Unrecognized",
])
def test_dangerous_proxy_headers_recognized_case_insensitively(header):
    assert has_untrusted_proxy_headers({header: "https, 127.0.0.1"})
    assert has_untrusted_proxy_headers({header.lower(): "https"})
    assert not has_untrusted_proxy_headers({"Origin": "https://127.0.0.1"})


@pytest.mark.parametrize("header,value", [
    ("X-Forwarded-Proto", "https"),
    ("Forwarded", "for=127.0.0.1;proto=https;host=localhost"),
    ("X-Forwarded-For", "127.0.0.1, 203.0.113.4"),
    ("X-Forwarded-Host", "127.0.0.1"),
    ("X-Forwarded-Prefix", "/api"),
    ("X-Real-IP", "127.0.0.1"),
    ("X-Original-Host", "localhost"),
])
@pytest.mark.parametrize("route", [
    "/api/v1/dev/auto/vehicles",
    "/api/v1/dev/auto/links/00000000-0000-0000-0000-000000000001/issue-proof",
])
def test_reject_before_any_database_access(monkeypatch, header, value, route):
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTH_API", "1")
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTO_API", "1")
    monkeypatch.setenv(
        "ROBOport_DATABASE_URL",
        "postgresql://unused:unused@127.0.0.1:5432/roboport",
    )

    def must_not_connect(*args, **kwargs):
        raise AssertionError("proxy spoof must be rejected before database use")

    monkeypatch.setattr(psycopg, "connect", must_not_connect)
    http = TestClient(app, base_url="https://127.0.0.1")
    headers = {header: value, "Origin": "https://127.0.0.1"}
    if "/issue-proof" in route:
        response = http.post(route, headers=headers)
    else:
        response = http.get(route, headers=headers)
    assert response.status_code == 404
    assert response.json()["code"] == "NOT_FOUND"
    assert response.headers["cache-control"] == "no-store"
    assert "requestId" in response.json()
    assert value not in response.text


def test_normal_auto_request_is_not_accidentally_blocked(monkeypatch):
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTH_API", "1")
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTO_API", "1")
    monkeypatch.setenv(
        "ROBOport_DATABASE_URL",
        "postgresql://unused:unused@127.0.0.1:5432/roboport",
    )

    def reached_db(*args, **kwargs):
        raise RuntimeError("reached live route")

    monkeypatch.setattr(psycopg, "connect", reached_db)
    with pytest.raises(RuntimeError, match="reached live route"):
        TestClient(app, base_url="https://127.0.0.1").get(
            "/api/v1/dev/auto/vehicles"
        )
    assert TestClient(app).get(
        "/api/v1/meta", headers={"X-Forwarded-For": "203.0.113.4"},
    ).status_code == 200


def test_packaged_uvicorn_explicitly_disables_proxy_header_interpretation():
    dockerfile = (Path(__file__).resolve().parents[2] /
                  "apps/api/Dockerfile").read_text(encoding="utf-8")
    assert "uvicorn roboport_api.main:app" in dockerfile
    assert "--no-proxy-headers" in dockerfile
    assert "--forwarded-allow-ips=*" not in dockerfile
