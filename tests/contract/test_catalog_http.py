"""Исторический draft robot catalog и отрицательная runtime HTTP-проверка (#165).

Наличие каталога в архивном контракте НЕ означает регистрацию endpoint.
"""
from pathlib import Path

import yaml
from fastapi.testclient import TestClient

from roboport_api.main import app

CONTRACT = yaml.safe_load(
    (Path(__file__).resolve().parents[2] / "contracts/openapi.yaml").read_text(encoding="utf-8")
)
client = TestClient(app)


def test_public_catalog_routes_are_described_in_versioned_contract():
    for path in (
        "/api/v1/catalog/brands",
        "/api/v1/catalog/models",
        "/api/v1/catalog/models/{modelId}",
    ):
        assert path in CONTRACT["paths"], f"Missing contract route: {path}"
        assert "get" in CONTRACT["paths"][path], f"Missing GET contract: {path}"
        assert CONTRACT["paths"][path]["get"].get("operationId")


def test_retired_catalog_rejects_all_http_methods():
    for path in ("/api/v1/catalog/brands", "/api/v1/catalog/models"):
        for method in ("GET", "POST", "PUT", "PATCH", "DELETE"):
            response = client.request(method, path)
            assert response.status_code == 404, (method, path)
            assert response.json()["code"] == "NOT_FOUND"
            assert response.headers["cache-control"] == "no-store"


def test_retired_catalog_not_in_live_openapi():
    paths = client.get("/openapi.json").json()["paths"]
    assert not any(path.startswith("/api/v1/catalog/") for path in paths)


def test_unknown_model_does_not_disclose_private_data():
    response = client.get("/api/v1/catalog/models/unknown-model")
    assert response.status_code == 404
    assert "customer" not in response.text.lower()
    assert "password" not in response.text.lower()


def test_unimplemented_customer_route_never_leaks_mock_account():
    response = client.get("/api/v1/customer/me")
    assert response.status_code in (401, 403, 404)
    assert "customerId" not in response.text
