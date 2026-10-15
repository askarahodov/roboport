"""Проверки HTTP-поведения трёх публичных маршрутов каталога.

Тесты не объявляют проектные 42 операции реализованными.
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


def test_synthetic_catalog_read_only():
    for path in ("/api/v1/catalog/brands", "/api/v1/catalog/models"):
        response = client.get(path)
        assert response.status_code == 200
        assert response.json() == {'items': []}
        assert client.post(path, json={}).status_code == 405


def test_unknown_model_does_not_disclose_private_data():
    response = client.get("/api/v1/catalog/models/unknown-model")
    assert response.status_code == 404
    assert "customer" not in response.text.lower()
    assert "password" not in response.text.lower()


def test_unimplemented_customer_route_never_leaks_mock_account():
    response = client.get("/api/v1/customer/me")
    assert response.status_code in (401, 403, 404)
    assert "customerId" not in response.text
