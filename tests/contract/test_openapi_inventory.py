"""Repository-level contract inventory, boundary and compatibility regressions."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from scripts.check_openapi_inventory import (
    REQUIRED, base_contracts, check_inventory, current_contracts, deep_changes,
    load_contract, private_boundary,
)


def document(private=True):
    path = "/api/v1/dev/auto/example" if private else "/health"
    return {
        "openapi": "3.1.0", "info": {"title": "Fixture", "version": "1"},
        "x-synthetic-only": private, "servers": [{"url": "https://127.0.0.1"}],
        "components": {"securitySchemes": {"localSession": {
            "type": "apiKey", "in": "cookie", "name": "rp_session"}},
            "schemas": {"Payload": {"type": "object", "properties": {
                "items": {"type": "array", "items": {"type": "object", "properties": {
                    "count": {"type": "integer"}, "label": {"type": "string"}}}}}}}},
        "paths": {path: {"post": {"operationId": "fixture", "security": [{"localSession": []}],
            "requestBody": {"content": {"application/json": {
                "schema": {"$ref": "#/components/schemas/Payload"}}}},
            "responses": {"200": {"description": "OK", "headers": {
                "Cache-Control": {"schema": {"type": "string"}}},
                "content": {"application/json": {"schema": {"type": "object",
                    "properties": {"ok": {"type": "boolean"}}}}}}}}}},
    }


PRIVATE = "contracts/auto-example-dev-openapi.json"


def inventory():
    return {"contracts/openapi.yaml": json.dumps(document(False)),
            "contracts/auto-dev-openapi.yaml": json.dumps(document()),
            PRIVATE: json.dumps(document())}


def test_all_repository_contracts_validate_and_are_not_public_runtime():
    from roboport_api.main import app
    root = Path(__file__).resolve().parents[2]
    files = current_contracts(root)
    assert REQUIRED <= files.keys()
    assert check_inventory(files) == []
    public_paths = app.openapi()["paths"]
    for name, raw in files.items():
        if name != "contracts/openapi.yaml":
            assert not set(load_contract(raw)["paths"]) & set(public_paths)


def test_new_contract_included_and_deleted_private_contract_blocks():
    old = inventory()
    new = dict(old)
    new["contracts/auto-new-dev-openapi.json"] = json.dumps(document())
    assert check_inventory(new, old) == []
    del new[PRIVATE]
    assert f"REMOVED_CONTRACT:{PRIVATE}" in check_inventory(new, old)
    del new["contracts/openapi.yaml"]
    assert "MISSING_REQUIRED_CONTRACT:contracts/openapi.yaml" in check_inventory(new, old)


@pytest.mark.parametrize("mutate,reason", [
    (lambda d: d.update({"x-synthetic-only": False}), "PRIVATE_ROOT_BOUNDARY"),
    (lambda d: d["servers"][0].update(url="https://public.example.org"), "PRIVATE_ROOT_BOUNDARY"),
    (lambda d: next(iter(d["paths"].values())).update(servers=[{"url": "https://public.example.org"}]), "PRIVATE_PATH_SERVER"),
    (lambda d: next(iter(d["paths"].values()))["post"].update(security=[]), "PRIVATE_OPERATION_SECURITY"),
    (lambda d: next(iter(d["paths"].values()))["post"].update(servers=[{"url": "https://public.example.org"}]), "PRIVATE_OPERATION_SERVER"),
    (lambda d: d["components"]["securitySchemes"]["localSession"].update(name="another_cookie"), "PRIVATE_SESSION_SCHEME"),
])
def test_private_boundary_cannot_be_overridden(mutate, reason):
    doc = document()
    mutate(doc)
    assert any(x.startswith(reason) for x in private_boundary(doc))
    files = inventory()
    files[PRIVATE] = json.dumps(doc)
    assert check_inventory(files)


def test_nested_shape_body_media_headers_security_and_id_breaks():
    path = "/api/v1/dev/auto/example"
    old = document()
    for mutate, reason in (
        (lambda d: d["components"]["schemas"]["Payload"]["properties"]["items"]["items"]["properties"].pop("label"), "REMOVED_NESTED_PROPERTY"),
        (lambda d: d["components"]["schemas"]["Payload"]["properties"]["items"]["items"].update(required=["count"]), "NEW_REQUIRED_SCHEMA_PROPERTY"),
        (lambda d: d["components"]["schemas"]["Payload"]["properties"]["items"]["items"]["properties"]["count"].update(type="string"), "CHANGED_SCHEMA_type"),
        (lambda d: d["paths"][path]["post"]["requestBody"].update(required=True), "NEW_REQUIRED_BODY"),
        (lambda d: d["paths"][path]["post"]["requestBody"]["content"].pop("application/json"), "REMOVED_MEDIA_TYPE"),
        (lambda d: d["paths"][path]["post"]["responses"]["200"]["headers"].pop("Cache-Control"), "REMOVED_RESPONSE_HEADER"),
        (lambda d: d["paths"][path]["post"]["responses"]["200"]["content"]["application/json"]["schema"]["properties"].pop("ok"), "REMOVED_NESTED_PROPERTY"),
        (lambda d: d["paths"][path]["post"].update(security=[]), "CHANGED_SECURITY"),
        (lambda d: d["paths"][path]["post"].update(operationId="renamed"), "CHANGED_OPERATION_ID"),
    ):
        new = deepcopy(old)
        mutate(new)
        assert any(x.startswith(reason) for x in deep_changes(old, new)), reason
    new = deepcopy(old)
    new["components"]["schemas"]["Payload"]["properties"]["items"]["items"]["properties"]["extra"] = {"type": "string"}
    assert deep_changes(old, new) == []


def test_untrusted_refs_duplicate_keys_and_cycles_fail_without_fetching():
    for raw in (
        '{"openapi":"3.1.0","openapi":"3.0.0"}',
        "openapi: 3.1.0\npaths: &loop {loop: *loop}",
        json.dumps({**document(), "x-untrusted": {"$ref": "https://external.example.org/schema"}}),
    ):
        with pytest.raises(Exception):
            load_contract(raw)


def test_base_inventory_reads_exact_git_commit_and_includes_deleted_files(tmp_path):
    import subprocess

    def git(*args):
        return subprocess.check_output(["git", "-C", str(tmp_path), *args], text=True).strip()

    git("init", "-q")
    git("config", "user.name", "Fixture")
    git("config", "user.email", "fixture@example.invalid")
    (tmp_path / "contracts").mkdir()
    for name, raw in inventory().items():
        (tmp_path / name).write_text(raw, encoding="utf-8")
    (tmp_path / "contracts/operation_ids.json").write_text("{}", encoding="utf-8")
    git("add", ".")
    git("commit", "-qm", "fixture")
    sha = git("rev-parse", "HEAD")
    (tmp_path / PRIVATE).unlink()
    assert base_contracts(tmp_path, sha) == inventory()
    assert f"REMOVED_CONTRACT:{PRIVATE}" in check_inventory(
        current_contracts(tmp_path), base_contracts(tmp_path, sha))
    with pytest.raises(ValueError):
        base_contracts(tmp_path, "--help")
