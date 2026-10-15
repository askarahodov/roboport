"""Контракт безопасности до реализации PostgreSQL-сессий (#13).

Не заменяет интеграционные тесты login, CSRF и ACL: пока нет auth handlers.
"""
from copy import deepcopy
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from roboport_api.main import app

SPEC = yaml.safe_load((Path(__file__).resolve().parents[2] / "contracts/openapi.yaml").read_text(encoding="utf-8"))
METHODS = {"get", "post", "put", "patch", "delete"}
PUBLIC = {
    ("/api/v1/catalog/brands", "get"),
    ("/api/v1/catalog/models", "get"),
    ("/api/v1/catalog/models/{modelId}", "get"),
}
CLIENT = TestClient(app)


def security_for(spec, path, method):
    return spec["paths"][path][method].get("security", spec.get("security"))


def guard_security(spec):
    scheme = spec["components"]["securitySchemes"]["localSession"]
    assert scheme["type"] == "apiKey"
    assert scheme["in"] == "cookie"
    assert scheme["name"] == "rp_session"
    for path, methods in spec["paths"].items():
        for method in METHODS & methods.keys():
            security = security_for(spec, path, method)
            assert security is not None, (method, path, "missing inherited security")
            if (path, method) in PUBLIC:
                assert security == [], (method, path, "catalog must remain public")
            elif path.startswith("/api/v1/customer/") or path.startswith("/api/v1/staff/"):
                assert {"localSession": []} in security, (method, path, "cookie guard missing")


def test_private_operations_require_server_cookie_by_contract():
    guard_security(SPEC)


def test_mutation_disabling_client_cookie_security_is_rejected():
    broken = deepcopy(SPEC)
    broken["paths"]["/api/v1/customer/me"]["get"]["security"] = []
    with pytest.raises(AssertionError, match="cookie guard missing"):
        guard_security(broken)


def test_unauthenticated_customer_api_cannot_disclose_profile():
    response = CLIENT.get("/api/v1/customer/me", headers={"X-User-ID": "fake", "X-Customer-ID": "fake"})
    assert response.status_code in (401, 403, 404)
    assert "customerId" not in response.text


def test_synthetic_auth_handlers_remain_forbidden_without_opt_in():
    # HTTP handlers доступны только по отдельному opt-in и loopback.
    for path in ("/api/v1/auth/login", "/api/v1/auth/logout", "/api/v1/auth/change-password"):
        assert CLIENT.post(path, json={}).status_code == 403
    assert CLIENT.get("/api/v1/auth/me").status_code == 403
