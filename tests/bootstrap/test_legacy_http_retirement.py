"""Production app must have no callable robotics routes even with old dev flags.

An immutable historical HTTP harness lives in tests/legacy_runtime.py and is
never mounted in the deployable roboport_api.main app.
"""
import pytest
from fastapi.testclient import TestClient

from roboport_api.main import app


RETIRED_URLS = (
    "/api/v1/catalog/brands",
    "/api/v1/catalog/models",
    "/api/v1/catalog/models/robot",
    "/api/v1/dev/tickets",
    "/api/v1/dev/tickets/keyset",
    "/api/v1/dev/tickets/11111111-1111-4111-8111-111111111111",
    "/api/v1/dev/tickets/11111111-1111-4111-8111-111111111111/transition",
    "/api/v1/dev/authorized/tickets/11111111-1111-4111-8111-111111111111",
    "/api/v1/dev/attachments/11111111-1111-4111-8111-111111111111/content",
    "/api/v1/dev/catalog/models/exact",
    "/api/v1/dev/catalog/parts/11111111-1111-4111-8111-111111111111/review",
    "/api/v1/dev/inventory/reservations",
    "/api/v1/dev/account/tickets",
    "/api/v1/dev/account/summary/robots",
    "/api/v1/dev/account/preferences",
    "/api/v1/dev/staff/queue",
    "/api/v1/dev/staff/audit",
    "/api/v1/dev/guest-intake",
    "/api/v1/dev/guest-claims/review-queue",
    "/api/v1/customer/estimates/11111111-1111-4111-8111-111111111111/approve",
    "/api/v1/staff/tickets/11111111-1111-4111-8111-111111111111/estimates",
)


@pytest.mark.parametrize("path", RETIRED_URLS)
@pytest.mark.parametrize("method", ("GET", "POST"))
def test_legacy_url_cannot_be_revived_with_synthetic_flags(monkeypatch, path, method):
    for flag in ("ROBOport_SYNTHETIC_ACCESS_API", "ROBOport_SYNTHETIC_AUTH_API",
                 "ROBOport_SYNTHETIC_AUTO_API"):
        monkeypatch.setenv(flag, "1")
    monkeypatch.setenv("ROBOport_DATABASE_URL",
                       "postgresql://nobody:invalid@127.0.0.1:1/unused")
    response = TestClient(app, base_url="https://127.0.0.1").request(method, path)
    assert response.status_code == 404, (method, path, response.text)


def test_registered_app_routes_have_no_retired_robot_surface():
    routes = {getattr(route, "path", "") for route in app.routes}
    assert all(path not in routes for path in (
        "/api/v1/dev/tickets",
        "/api/v1/dev/tickets/keyset",
        "/api/v1/dev/account/preferences",
        "/api/v1/dev/staff/queue",
        "/api/v1/dev/staff/audit",
        "/api/v1/dev/guest-intake",
        "/api/v1/dev/catalog/models/exact",
        "/api/v1/staff/tickets/{ticketId}/estimates",
        "/api/v1/customer/estimates/{estimateId}/approve",
    ))
    live_schema = TestClient(app).get("/openapi.json").json()
    assert not any("/tickets" in path or "/catalog" in path or "/guest-claims" in path
                   for path in live_schema["paths"])


def test_legacy_http_regression_harness_is_separate_object():
    from tests.legacy_runtime import app as archived
    assert archived is not app
    live = {getattr(route, "path", "") for route in app.routes}
    historic = {getattr(route, "path", "") for route in archived.routes}
    assert "/api/v1/dev/tickets" in historic - live
    from roboport_api.auth_http import legacy_router
    raw = {getattr(route, "path", "") for route in legacy_router.routes}
    assert "/api/v1/dev/staff/queue" in raw, sorted(raw)
    assert "/api/v1/dev/staff/queue" in historic - live, {"raw": sorted(raw), "historic_staff": sorted(p for p in historic if "/staff/" in p), "live_staff": sorted(p for p in live if "/staff/" in p)}
