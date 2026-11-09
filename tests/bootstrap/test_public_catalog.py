"""Automotive public boundary: retired robot catalog must be unreachable (#165)."""
from fastapi.testclient import TestClient

from roboport_api.main import app


def test_robot_catalog_urls_are_unregistered_for_all_http_methods():
    client = TestClient(app)
    for path in (
        "/api/v1/catalog/brands",
        "/api/v1/catalog/models",
        "/api/v1/catalog/models/example",
    ):
        for method in ("GET", "POST", "PUT", "PATCH", "DELETE"):
            response = client.request(method, path)
            assert response.status_code == 404, (method, path, response.text)
            assert response.json()["code"] == "NOT_FOUND"


def test_robot_catalog_is_not_in_live_fastapi_openapi():
    schema = TestClient(app).get("/openapi.json").json()
    assert schema["info"]["title"] == "Airmatik automotive development API"
    assert not any(path.startswith("/api/v1/catalog/")
                   for path in schema["paths"])
    assert not any("/robots" in path or "/devices" in path
                   for path in schema["paths"])


def test_runtime_service_identity_is_automotive():
    result = TestClient(app).get("/api/v1/meta")
    assert result.status_code == 200
    assert result.json() == {"service": "Airmatik", "environment": "development"}
