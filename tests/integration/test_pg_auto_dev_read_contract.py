"""Проверка ответов реального PostgreSQL HTTP относительно dev-only OpenAPI."""
from pathlib import Path

import psycopg
import yaml
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator, FormatChecker

from roboport_api.main import app
from roboport_api.session_tokens import token_digest
from test_pg_auto_vehicle_http import _seed
from test_pg_baseline import DSN


SPEC = yaml.safe_load(
    (Path(__file__).resolve().parents[2] / "contracts/auto-dev-openapi.yaml")
    .read_text(encoding="utf-8")
)


def _assert_schema(payload, name):
    schema = {**SPEC, "$ref": "#/components/schemas/" + name}
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(payload)


def test_live_auto_dev_read_contract_and_bola(monkeypatch):
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTO_API", "1")
    monkeypatch.setenv("ROBOport_SYNTHETIC_AUTH_API", "1")
    monkeypatch.setenv("ROBOport_DATABASE_URL", DSN)
    a, b, ca, cb, va, vb, pending, (token_a, token_b), vin = _seed()
    http = TestClient(app, base_url="https://127.0.0.1")
    path = "/api/v1/dev/auto/vehicles"
    unauthorized = http.get(path)
    assert unauthorized.status_code == 401
    _assert_schema(unauthorized.json(), "AutoDevError")
    http.cookies.set("rp_session", token_a)

    found = http.get(path + "?limit=1&offset=0")
    assert found.status_code == 200
    assert found.headers["cache-control"] == "no-store"
    _assert_schema(found.json(), "AutoVehiclePage")
    assert found.json()["items"][0]["vinLast4"] == vin[-4:]
    assert {item["id"] for item in found.json()["items"]} == {str(va)}

    detail = http.get(path + "/" + str(va))
    assert detail.status_code == 200
    _assert_schema(detail.json(), "AutoVehicle")
    for ident in (vb, pending):
        hidden = http.get(path + "/" + str(ident))
        assert hidden.status_code == 404
        _assert_schema(hidden.json(), "AutoDevError")
    for query in ("limit=foo", "limit=-1", "limit=100000000000",
                  "offset=abc", "offset=-1", "offset=10001",
                  "limit=1&limit=2", "offset=0&offset=1"):
        invalid = http.get(path + "?" + query)
        assert invalid.status_code == 422, query
        _assert_schema(invalid.json(), "AutoDevError")
        assert invalid.json()["code"] == "INVALID_INPUT"

    # База проверяет доступ к машине в каждом запросе; 200 [] после отзыва
    # — нормальный ответ, а не повод оставлять старые карточки на клиенте.
    with psycopg.connect(DSN) as db:
        db.execute("UPDATE account_customer_link SET revoked_at=now() WHERE user_id=%s", (a,))
    empty = http.get(path)
    assert empty.status_code == 200
    assert empty.json() == {"items": []}
    _assert_schema(empty.json(), "AutoVehiclePage")
    hidden = http.get(path + "/" + str(va))
    assert hidden.status_code == 404
    _assert_schema(hidden.json(), "AutoDevError")

    with psycopg.connect(DSN) as db:
        db.execute("UPDATE user_session SET revoked_at=now() WHERE token_hash=%s",
                   (token_digest(token_a),))
    rejected = http.get(path)
    assert rejected.status_code == 401
    _assert_schema(rejected.json(), "AutoDevError")
