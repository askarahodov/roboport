"""Static legacy route inventory regression checks (#165)."""
from pathlib import Path
from tempfile import TemporaryDirectory

from scripts.inventory_legacy_routes import inventory, main, scan_openapi_contract, scan_python


def test_api_route_metadata_and_hidden_decorator():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        path = root / "router.py"
        path.write_text(
            'from fastapi import APIRouter\n'
            'router = APIRouter(prefix="/api/v1/dev")\n'
            '@router.get("/tickets", include_in_schema=False)\n'
            'def read(): pass\n'
            '@router.post("/auto/work-orders")\n'
            'async def write(): pass\n',
            encoding="utf-8",
        )
        rows = scan_python(path, root)
        assert len(rows) == 2
        assert rows[0]["path"] == "/api/v1/dev/tickets"
        assert rows[0]["legacy_candidate"] is True
        assert rows[0]["schema_hidden"] is True
        assert rows[1]["path"] == "/api/v1/dev/auto/work-orders"
        assert rows[1]["legacy_candidate"] is False


def test_web_route_and_navigation_inventory_is_deterministic():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "apps/api/roboport_api").mkdir(parents=True)
        web = root / "apps/web/src"
        web.mkdir(parents=True)
        (web / "app.tsx").write_text(
            '<Route path="/catalog/models" element={<X />} />\n'
            '<NavLink to="/staff/auto-calendar">Календарь</NavLink>\n',
            encoding="utf-8",
        )
        rows = inventory(root)
        assert rows == inventory(root)
        assert [r["kind"] for r in rows] == ["web-route", "web-navigation"]
        assert rows[0]["legacy_candidate"] is True
        assert rows[1]["legacy_candidate"] is False


def test_invalid_python_syntax_is_not_silently_ignored():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        path = root / "broken.py"
        path.write_text("def ???\n", encoding="utf-8")
        import pytest
        with pytest.raises(SyntaxError):
            scan_python(path, root)



def test_constant_routes_prefix_composition_and_router_visibility():
    """Real auto modules use PREFIX + "/export"; do not silently omit them."""
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        source = root / "example.py"
        source.write_text(
            'from fastapi import APIRouter\n'
            'ROOT = "/api/v1/dev/auto"\n'
            'PREFIX: str = ROOT + "/organizations/{org_id}"\n'
            'router = APIRouter(prefix=PREFIX, include_in_schema=False)\n'
            '@router.get("/reports")\n'
            'def report(): pass\n'
            '@router.get(path="/reports" + "/export")\n'
            'def export(): pass\n'
            '@router.api_route(path=f"{ROOT}/check")\n'
            'def other(): pass\n'
            'router.add_api_route("/manual", lambda: None)\n',
            encoding="utf-8",
        )
        rows = scan_python(source, root)
        assert [r["path"] for r in rows] == [
            "/api/v1/dev/auto/organizations/{org_id}/reports",
            "/api/v1/dev/auto/organizations/{org_id}/reports/export",
            "/api/v1/dev/auto/organizations/{org_id}/api/v1/dev/auto/check",
            "/api/v1/dev/auto/organizations/{org_id}/manual",
        ]
        assert all(r["schema_hidden"] and not r["unresolved"] for r in rows)


def test_dynamic_or_reassigned_route_cannot_pass_strict_inventory(capsys):
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        api = root / "apps/api/roboport_api"
        api.mkdir(parents=True)
        (api / "dynamic.py").write_text(
            'from fastapi import APIRouter\n'
            'PREFIX = "/api/v1/dev"\n'
            'PREFIX = get_prefix()\n'
            'router = APIRouter(prefix=PREFIX)\n'
            '@router.get("/test")\n'
            'def test(): pass\n',
            encoding="utf-8",
        )
        rows = inventory(root)
        assert len(rows) == 1
        assert rows[0]["unresolved"] is True
        assert rows[0]["legacy_candidate"] is True
        assert rows[0]["review"] == "path-unresolved"
        assert main(["--root", str(root), "--check"]) == 1
        report = capsys.readouterr().out
        assert '"apiPathsResolved": false' in report
        assert "get_prefix" not in report


def test_repository_routes_with_named_prefix_are_included():
    """Protect real customer-facing reports from disappearing from the audit."""
    root = Path(__file__).resolve().parents[2]
    source = root / "apps/api/roboport_api/auto_closed_order_ledger_reconciliation.py"
    rows = scan_python(source, root)
    assert {r["path"] for r in rows} == {
        "/api/v1/dev/auto/organizations/{org_id}/locations/{loc_id}"
        "/reports/closed-order-ledger-reconciliation",
        "/api/v1/dev/auto/organizations/{org_id}/locations/{loc_id}"
        "/reports/closed-order-ledger-reconciliation/export",
    }
    assert all(r["schema_hidden"] and not r["unresolved"] for r in rows)



def test_second_static_assignment_is_not_mistaken_for_route_time_value():
    """A post-decorator reassignment cannot rewrite the historical URL."""
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        file = root / "route.py"
        file.write_text(
            'from fastapi import APIRouter\n'
            'PREFIX = "/api/v1/dev/tickets"\n'
            'router = APIRouter()\n'
            '@router.get(PREFIX)\n'
            'def old(): pass\n'
            'PREFIX = "/api/v1/dev/auto/vehicles"\n',
            encoding="utf-8",
        )
        rows = scan_python(file, root)
        assert len(rows) == 1
        assert rows[0]["unresolved"] is True
        assert rows[0]["legacy_candidate"] is True


def test_conditional_module_reassignment_does_not_hide_legacy_route():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        file = root / "route.py"
        file.write_text(
            'from fastapi import APIRouter\n'
            'PREFIX = "/api/v1/dev/tickets"\n'
            'if condition:\n'
            '    PREFIX = "/api/v1/dev/auto/vehicles"\n'
            'router = APIRouter()\n'
            '@router.get(PREFIX)\n'
            'def old(): pass\n',
            encoding="utf-8",
        )
        rows = scan_python(file, root)
        assert len(rows) == 1
        assert rows[0]["review"] == "path-unresolved"



def test_unknown_router_owner_and_annotated_dynamic_prefix_fail_closed():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        file = root / "routes.py"
        file.write_text(
            'from fastapi import APIRouter\n'
            'router: APIRouter = APIRouter(prefix=get_prefix())\n'
            '@router.get("/export")\n'
            'def export(): pass\n'
            'get_router().add_api_route(get_path(), handler)\n',
            encoding="utf-8",
        )
        rows = scan_python(file, root)
        assert len(rows) == 2
        assert all(row["unresolved"] and row["legacy_candidate"] for row in rows)
        assert [row["method"] for row in rows] == ["GET", "API_ROUTE"]


def test_unknown_imported_router_is_not_assumed_to_have_empty_prefix():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        file = root / "routes.py"
        file.write_text(
            'from unknown import router\n'
            '@router.get("/safe-looking-path")\n'
            'def export(): pass\n',
            encoding="utf-8",
        )
        rows = scan_python(file, root)
        assert rows[0]["path"] == "<unresolved>"
        assert rows[0]["review"] == "path-unresolved"



def test_nested_function_local_prefix_does_not_resolve_to_module_constant():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        file = root / "shadow.py"
        file.write_text(
            'from fastapi import APIRouter\n'
            'PREFIX = "/api/v1/dev/auto"\n'
            'router = APIRouter()\n'
            'def mount(PREFIX):\n'
            '    @router.get(PREFIX + "/export")\n'
            '    def handler(): pass\n'
            'mount("/api/v1/dev/tickets")\n',
            encoding="utf-8",
        )
        rows = scan_python(file, root)
        assert len(rows) == 1
        assert rows[0]["path"] == "<unresolved>"
        assert rows[0]["legacy_candidate"] is True


def test_dynamic_kwargs_router_settings_are_unresolved():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        file = root / "dynamic_kwargs.py"
        file.write_text(
            'from fastapi import APIRouter\n'
            'router = APIRouter(**get_router_settings())\n'
            '@router.get("/export")\n'
            'def handler(): pass\n',
            encoding="utf-8",
        )
        rows = scan_python(file, root)
        assert rows[0]["unresolved"] is True
        assert rows[0]["review"] == "path-unresolved"


def test_local_imperative_registration_is_unresolved():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        file = root / "registration.py"
        file.write_text(
            'from fastapi import APIRouter\n'
            'PREFIX = "/api/v1/dev/auto"\n'
            'router = APIRouter()\n'
            'def mount(PREFIX):\n'
            '    router.add_api_route(PREFIX + "/export", handler)\n',
            encoding="utf-8",
        )
        rows = scan_python(file, root)
        assert len(rows) == 1
        assert rows[0]["unresolved"] is True



def test_factory_decorator_cannot_disappear_from_route_inventory():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        file = root / "factory.py"
        file.write_text(
            '@get_router().get("/api/v1/dev/tickets")\n'
            'def old(): pass\n',
            encoding="utf-8",
        )
        rows = scan_python(file, root)
        assert len(rows) == 1
        assert rows[0]["unresolved"] is True
        assert rows[0]["legacy_candidate"] is True


def test_historical_openapi_draft_paths_are_inventoried_without_runtime_claims():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        contract = root / "contracts/openapi.yaml"
        contract.parent.mkdir(parents=True)
        contract.write_text(
            "openapi: 3.1.2\n"
            "info:\n  title: Example API (draft, synthetic only)\n"
            "  version: 1.2.3-draft\n"
            "servers:\n- url: https://api.example.invalid\n"
            "paths:\n"
            "  /api/v1/robot/devices:\n"
            "    get:\n      operationId: listRobotDevices\n"
            "  /api/v1/auto/vehicles/{vehicleId}:\n"
            "    get:\n      operationId: getVehicle\n",
            encoding="utf-8",
        )
        rows = scan_openapi_contract(contract, root)
        assert [(r["method"], r["path"], r["operation_id"]) for r in rows] == [
            ("GET", "/api/v1/robot/devices", "listRobotDevices"),
            ("GET", "/api/v1/auto/vehicles/{vehicleId}", "getVehicle"),
        ]
        assert rows[0]["legacy_candidate"] is True
        assert rows[1]["legacy_candidate"] is False
        assert all(r["contract_status"] == "historical-draft-not-runtime" for r in rows)
        assert all(not r["unresolved"] for r in rows)


def test_historical_openapi_contract_must_remain_non_publishable(capsys):
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        contract = root / "contracts/openapi.yaml"
        contract.parent.mkdir(parents=True)
        contract.write_text(
            "openapi: 3.1.2\ninfo:\n  title: Example API\n  version: 1.2.3\n"
            "servers:\n- url: https://api.example.com\n"
            "paths:\n  /api/v1/robot/devices:\n    get:\n      operationId: listRobotDevices\n",
            encoding="utf-8",
        )
        assert main(["--root", str(root), "--check"]) == 1
        report = capsys.readouterr().out
        assert '"historicalOpenApiDraftSafe": false' in report

        contract.write_text(
            "info:\n  title: Example API (draft)\n  version: 1.0-draft\n"
            "servers:\n- url: https://api.example.com/path.invalid\n"
            "paths:\n  /api/v1/robot/devices:\n    get:\n      operationId: listRobotDevices\n",
            encoding="utf-8",
        )
        assert main(["--root", str(root), "--check"]) == 1
        report = capsys.readouterr().out
        assert '"historicalOpenApiDraftSafe": false' in report


def test_real_historical_openapi_draft_is_included_in_inventory():
    root = Path(__file__).resolve().parents[2]
    rows = scan_openapi_contract(root / "contracts/openapi.yaml", root)
    operations = [r for r in rows if r["kind"] == "historical-openapi-draft"]
    assert len(operations) == 46
    assert any(r["path"] == "/api/v1/customer/devices/{deviceId}" for r in operations)
    assert all(r["contract_status"] == "historical-draft-not-runtime" for r in operations)
    assert not any(r["unresolved"] for r in rows)


def test_missing_openapi_operation_id_fails_closed():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        contract = root / "contracts/openapi.yaml"
        contract.parent.mkdir(parents=True)
        contract.write_text(
            "info:\n  title: Example API (draft)\n  version: 1.0-draft\n"
            "servers:\n- url: https://api.example.invalid\n"
            "paths:\n  /api/v1/robot/devices:\n    get:\n      summary: no operation id\n",
            encoding="utf-8",
        )
        rows = scan_openapi_contract(contract, root)
        assert len(rows) == 1
        assert rows[0]["unresolved"] is True
        assert rows[0]["review"] == "operation-id-missing"
        assert main(["--root", str(root), "--check"]) == 1


def test_server_inventory_rejects_live_url_after_description_field():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        contract = root / "contracts/openapi.yaml"
        contract.parent.mkdir(parents=True)
        contract.write_text(
            "info:\n  title: Example API (draft)\n  version: 1.0-draft\n"
            "servers:\n"
            "- url: https://api.example.invalid\n"
            "- description: live server\n"
            "  url: https://api.example.com\n"
            "paths:\n  /safe:\n    get:\n      operationId: safe\n",
            encoding="utf-8",
        )
        rows = scan_openapi_contract(contract, root)
        assert any(r["kind"] == "historical-openapi-control" and r["unresolved"] for r in rows)
        assert main(["--root", str(root), "--check"]) == 1


def test_quoted_paths_and_commented_methods_are_inventoried_and_unknown_keys_fail_closed():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        contract = root / "contracts/openapi.yaml"
        contract.parent.mkdir(parents=True)
        contract.write_text(
            "info:\n  title: Example API (draft)\n  version: 1.0-draft\n"
            "servers:\n- url: https://api.example.invalid\n"
            "paths:\n"
            "  /safe:\n"
            "    get:\n      operationId: safe\n"
            '  "/api/v1/robot/devices":\n'
            "    post: # comment after method key\n"
            "      operationId: listRobotDevices\n"
            "  /safe-two:\n"
            "    get : # malformed method key\n"
            "      operationId: must-not-attach-to-previous-method\n"
            "    get:\n      operationId: safeTwo\n",
            encoding="utf-8",
        )
        rows = scan_openapi_contract(contract, root)
        operations = [r for r in rows if r["kind"] == "historical-openapi-draft"]
        assert any(r["path"] == "/api/v1/robot/devices" and
                   r["method"] == "POST" and
                   r["operation_id"] == "listRobotDevices" for r in operations)
        assert not any(r["operation_id"] == "must-not-attach-to-previous-method" for r in operations)
        assert any(r["unresolved"] and r["review"] == "malformed-method-key" for r in rows)
        assert main(["--root", str(root), "--check"]) == 1


def test_unrecognized_quoted_path_key_resets_current_path_instead_of_inheriting_previous():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        contract = root / "contracts/openapi.yaml"
        contract.parent.mkdir(parents=True)
        contract.write_text(
            "info:\n  title: Example API (draft)\n  version: 1.0-draft\n"
            "servers:\n- url: https://api.example.invalid\n"
            "paths:\n"
            "  /safe:\n"
            "    get:\n      operationId: safe\n"
            '  "not-an-absolute-path":\n'
            "    post:\n      operationId: hiddenLegacy\n",
            encoding="utf-8",
        )
        rows = scan_openapi_contract(contract, root)
        assert any(r["review"] == "path-key-is-not-an-absolute-path" and r["unresolved"] for r in rows)
        assert not any(r.get("operation_id") == "hiddenLegacy" and r["path"] == "/safe" for r in rows)
        assert main(["--root", str(root), "--check"]) == 1


def test_server_inventory_rejects_nested_url_inside_description_object():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        contract = root / "contracts/openapi.yaml"
        contract.parent.mkdir(parents=True)
        contract.write_text(
            "info:\n  title: Example API (draft)\n  version: 1.0-draft\n"
            "servers:\n"
            "- description:\n"
            "    url: https://api.example.invalid\n"
            "paths:\n  /safe:\n    get:\n      operationId: safe\n",
            encoding="utf-8",
        )
        rows = scan_openapi_contract(contract, root)
        assert any(r["kind"] == "historical-openapi-control" and r["unresolved"] for r in rows)
        assert main(["--root", str(root), "--check"]) == 1


def test_quoted_path_escape_sequences_fail_closed_without_misclassifying_legacy_paths():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        contract = root / "contracts/openapi.yaml"
        contract.parent.mkdir(parents=True)
        contract.write_text(
            "info:\n  title: Example API (draft)\n  version: 1.0-draft\n"
            "servers:\n- url: https://api.example.invalid\n"
            "paths:\n"
            + r'  "/\q":' + "\n"
            + "    get:\n      operationId: invalidEscape\n"
            + r'  "/\u0072obot":' + "\n"
            + "    get:\n      operationId: encodedRobot\n",
            encoding="utf-8",
        )
        rows = scan_openapi_contract(contract, root)
        assert sum(r.get("review") == "path-key-contains-unsupported-escape" for r in rows) == 2
        assert not any(r.get("operation_id") in {"invalidEscape", "encodedRobot"} for r in rows)
        assert main(["--root", str(root), "--check"]) == 1


def test_openapi_method_keys_are_case_sensitive():
    for method in ("GET", "Get"):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            contract = root / "contracts/openapi.yaml"
            contract.parent.mkdir(parents=True)
            contract.write_text(
                "info:\n  title: Example API (draft)\n  version: 1.0-draft\n"
                "servers:\n- url: https://api.example.invalid\n"
                "paths:\n  /safe:\n"
                f"    {method}:\n"
                "      operationId: shouldNotBeResolved\n",
                encoding="utf-8",
            )
            rows = scan_openapi_contract(contract, root)
            assert any(r["unresolved"] and r["review"] == "unrecognized-path-item-key" for r in rows)
            assert not any(r.get("operation_id") == "shouldNotBeResolved" for r in rows)
            assert main(["--root", str(root), "--check"]) == 1


def test_path_item_and_operation_level_server_overrides_fail_closed():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        contract = root / "contracts/openapi.yaml"
        contract.parent.mkdir(parents=True)
        contract.write_text(
            "info:\n  title: Example API (draft)\n  version: 1.0-draft\n"
            "servers:\n- url: https://api.example.invalid\n"
            "paths:\n"
            "  /path-server:\n"
            "    servers:\n"
            "      - url: https://api.example.com\n"
            "    get:\n      operationId: pathServer\n"
            "  /operation-server:\n"
            "    get:\n"
            "      servers:\n"
            "        - url: https://api.example.com\n"
            "      operationId: operationServer\n",
            encoding="utf-8",
        )
        rows = scan_openapi_contract(contract, root)
        reviews = {r["review"] for r in rows if r["unresolved"]}
        assert "path-item-server-override-unreviewed" in reviews
        assert "operation-server-override-unreviewed" in reviews
        assert main(["--root", str(root), "--check"]) == 1


def test_inline_method_mapping_is_not_silently_ignored_or_attached_to_another_method():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        contract = root / "contracts/openapi.yaml"
        contract.parent.mkdir(parents=True)
        contract.write_text(
            "info:\n  title: Example API (draft)\n  version: 1.0-draft\n"
            "servers:\n- url: https://api.example.invalid\n"
            "paths:\n  /safe:\n"
            "    get: {operationId: inlineOperation}\n"
            "    post:\n      operationId: validOperation\n",
            encoding="utf-8",
        )
        rows = scan_openapi_contract(contract, root)
        assert any(r["unresolved"] and r["review"] == "unsupported-inline-method-value" for r in rows)
        assert any(r.get("operation_id") == "validOperation" for r in rows)
        assert not any(r.get("operation_id") == "inlineOperation" for r in rows)
        assert main(["--root", str(root), "--check"]) == 1


def test_operation_quoted_and_merge_keys_cannot_hide_server_overrides():
    for key_line in (
        '      "servers": [{url: https://api.example.com}]',
        r'      "\u0073ervers": [{url: https://api.example.com}]',
        "      <<: {servers: [{url: https://api.example.com}]}",
    ):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            contract = root / "contracts/openapi.yaml"
            contract.parent.mkdir(parents=True)
            contract.write_text(
                "info:\n  title: Example API (draft)\n  version: 1.0-draft\n"
                "servers:\n- url: https://api.example.invalid\n"
                "paths:\n  /safe:\n"
                "    get:\n      operationId: safe\n"
                + key_line + "\n",
                encoding="utf-8",
            )
            rows = scan_openapi_contract(contract, root)
            assert any(r["unresolved"] for r in rows)
            assert main(["--root", str(root), "--check"]) == 1


def test_root_servers_anchor_and_quoted_override_are_rejected_even_after_paths():
    for extra in (
        "servers: &live\n- url: https://api.example.com\n",
        '"servers":\n- url: https://api.example.com\n',
        "<<: {servers: [{url: https://api.example.com}]}\n",
    ):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            contract = root / "contracts/openapi.yaml"
            contract.parent.mkdir(parents=True)
            contract.write_text(
                "info:\n  title: Example API (draft)\n  version: 1.0-draft\n"
                "servers:\n- url: https://api.example.invalid\n"
                "paths:\n  /safe:\n    get:\n      operationId: safe\n"
                + extra,
                encoding="utf-8",
            )
            rows = scan_openapi_contract(contract, root)
            assert any(r["kind"] == "historical-openapi-control" and r["unresolved"] for r in rows)
            assert main(["--root", str(root), "--check"]) == 1


def test_servers_sequence_marker_requires_separator_after_dash():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        contract = root / "contracts/openapi.yaml"
        contract.parent.mkdir(parents=True)
        contract.write_text(
            "info:\n  title: Example API (draft)\n  version: 1.0-draft\n"
            "servers:\n-url: https://api.example.invalid\n"
            "paths:\n  /safe:\n    get:\n      operationId: safe\n",
            encoding="utf-8",
        )
        rows = scan_openapi_contract(contract, root)
        assert any(r["kind"] == "historical-openapi-control" and r["unresolved"] for r in rows)
        assert main(["--root", str(root), "--check"]) == 1


def test_operation_callbacks_fail_closed_because_nested_path_items_can_override_servers():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        contract = root / "contracts/openapi.yaml"
        contract.parent.mkdir(parents=True)
        contract.write_text(
            "info:\n  title: Example API (draft)\n  version: 1.0-draft\n"
            "servers:\n- url: https://api.example.invalid\n"
            "paths:\n  /safe:\n"
            "    get:\n      operationId: safe\n"
            "      callbacks:\n"
            "        onEvent:\n"
            "          '{$request.body#/callbackUrl}':\n"
            "            post:\n"
            "              servers:\n"
            "                - url: https://api.example.com\n",
            encoding="utf-8",
        )
        rows = scan_openapi_contract(contract, root)
        assert any(r["unresolved"] and r["review"] == "operation-callbacks-unreviewed" for r in rows)
        assert main(["--root", str(root), "--check"]) == 1


def test_root_escaped_servers_key_cannot_shadow_the_placeholder_server():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        contract = root / "contracts/openapi.yaml"
        contract.parent.mkdir(parents=True)
        contract.write_text(
            "info:\n  title: Example API (draft)\n  version: 1.0-draft\n"
            "servers:\n- url: https://api.example.invalid\n"
            "paths:\n  /safe:\n    get:\n      operationId: safe\n"
            + r'"\u0073ervers":' + "\n"
            + "- url: https://api.example.com\n",
            encoding="utf-8",
        )
        rows = scan_openapi_contract(contract, root)
        assert any(r["kind"] == "historical-openapi-control" and r["unresolved"] for r in rows)
        assert main(["--root", str(root), "--check"]) == 1


def test_root_paths_anchor_duplicate_cannot_hide_additional_operations():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        contract = root / "contracts/openapi.yaml"
        contract.parent.mkdir(parents=True)
        contract.write_text(
            "info:\n  title: Example API (draft)\n  version: 1.0-draft\n"
            "servers:\n- url: https://api.example.invalid\n"
            "paths:\n  /safe:\n    get:\n      operationId: safe\n"
            "paths: &extra\n  /robot:\n    get:\n      operationId: hiddenRobot\n",
            encoding="utf-8",
        )
        rows = scan_openapi_contract(contract, root)
        assert any(r["unresolved"] and r["review"] == "paths-section-duplicate" for r in rows)
        assert not any(r.get("operation_id") == "hiddenRobot" for r in rows)
        assert main(["--root", str(root), "--check"]) == 1


def test_draft_title_and_version_must_be_fields_of_the_single_root_info_object():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        contract = root / "contracts/openapi.yaml"
        contract.parent.mkdir(parents=True)
        contract.write_text(
            "info:\n  description: title and version intentionally absent\n"
            "servers:\n- url: https://api.example.invalid\n"
            "paths:\n  /safe:\n    get:\n      operationId: safe\n"
            "components:\n  title: Fake draft title\n  version: 1.0-draft\n",
            encoding="utf-8",
        )
        rows = scan_openapi_contract(contract, root)
        assert any(r["kind"] == "historical-openapi-control" and r["unresolved"] for r in rows)
        assert main(["--root", str(root), "--check"]) == 1


def test_root_explicit_yaml_key_syntax_cannot_hide_a_second_servers_section():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        contract = root / "contracts/openapi.yaml"
        contract.parent.mkdir(parents=True)
        contract.write_text(
            "info:\n  title: Example API (draft)\n  version: 1.0-draft\n"
            "servers:\n- url: https://api.example.invalid\n"
            "paths:\n  /safe:\n    get:\n      operationId: safe\n"
            "? servers\n"
            ":\n  - url: https://api.example.com\n",
            encoding="utf-8",
        )
        rows = scan_openapi_contract(contract, root)
        assert any(r["unresolved"] and r["kind"] == "historical-openapi-control" for r in rows)
        assert main(["--root", str(root), "--check"]) == 1


def test_path_keys_with_whitespace_or_query_are_not_reported_as_safe_routes():
    for path_key in ('  "/safe ":\n', '  "/safe?mode=all":\n'):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            contract = root / "contracts/openapi.yaml"
            contract.parent.mkdir(parents=True)
            contract.write_text(
                "info:\n  title: Example API (draft)\n  version: 1.0-draft\n"
                "servers:\n- url: https://api.example.invalid\n"
                "paths:\n" + path_key
                + "    get:\n      operationId: notSafe\n",
                encoding="utf-8",
            )
            rows = scan_openapi_contract(contract, root)
            assert any(r["unresolved"] and r["review"] == "path-key-is-not-an-absolute-path" for r in rows)
            assert not any(r.get("operation_id") == "notSafe" for r in rows)
            assert main(["--root", str(root), "--check"]) == 1


def test_root_anchored_or_tagged_server_keys_fail_closed_before_key_name():
    for hidden_server_key in ("&live servers:\n", "!!str servers:\n"):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            contract = root / "contracts/openapi.yaml"
            contract.parent.mkdir(parents=True)
            contract.write_text(
                "info:\n  title: Example API (draft)\n  version: 1.0-draft\n"
                "servers:\n- url: https://api.example.invalid\n"
                "paths:\n  /safe:\n    get:\n      operationId: safe\n"
                + hidden_server_key
                + "- url: https://api.example.com\n",
                encoding="utf-8",
            )
            rows = scan_openapi_contract(contract, root)
            assert any(r["kind"] == "historical-openapi-control" and r["unresolved"] for r in rows)
            assert main(["--root", str(root), "--check"]) == 1


def test_yaml_document_separator_with_comment_cannot_hide_second_openapi_document():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        contract = root / "contracts/openapi.yaml"
        contract.parent.mkdir(parents=True)
        contract.write_text(
            "info:\n  title: Example API (draft)\n  version: 1.0-draft\n"
            "servers:\n- url: https://api.example.invalid\n"
            "paths:\n  /safe:\n    get:\n      operationId: safe\n"
            "--- # second document\n"
            "  servers:\n  - url: https://api.example.com\n"
            "  paths:\n    /robot:\n      get:\n        operationId: hiddenRobot\n",
            encoding="utf-8",
        )
        rows = scan_openapi_contract(contract, root)
        assert any(r["kind"] == "historical-openapi-control" and r["unresolved"] for r in rows)
        assert not any(r.get("operation_id") == "hiddenRobot" for r in rows)
        assert main(["--root", str(root), "--check"]) == 1


def test_openapi_webhooks_section_fails_closed_until_it_is_inventoried():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        contract = root / "contracts/openapi.yaml"
        contract.parent.mkdir(parents=True)
        contract.write_text(
            "info:\n  title: Example API (draft)\n  version: 1.0-draft\n"
            "servers:\n- url: https://api.example.invalid\n"
            "paths:\n  /safe:\n    get:\n      operationId: safe\n"
            "webhooks:\n  robotEvent:\n    post:\n      operationId: hiddenWebhook\n",
            encoding="utf-8",
        )
        rows = scan_openapi_contract(contract, root)
        assert any(r["unresolved"] and r["review"] == "webhooks-section-unreviewed" for r in rows)
        assert not any(r.get("operation_id") == "hiddenWebhook" for r in rows)
        assert main(["--root", str(root), "--check"]) == 1


def test_percent_encoded_path_keys_fail_closed_instead_of_hiding_legacy_segments():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        contract = root / "contracts/openapi.yaml"
        contract.parent.mkdir(parents=True)
        contract.write_text(
            "info:\n  title: Example API (draft)\n  version: 1.0-draft\n"
            "servers:\n- url: https://api.example.invalid\n"
            "paths:\n"
            + r'  "/r%6fbot/devices":' + "\n"
            + "    get:\n      operationId: encodedRobot\n",
            encoding="utf-8",
        )
        rows = scan_openapi_contract(contract, root)
        assert any(r["unresolved"] and r["review"] == "path-key-percent-encoding-unreviewed" for r in rows)
        assert not any(r.get("operation_id") == "encodedRobot" for r in rows)
        assert main(["--root", str(root), "--check"]) == 1


def test_mapping_header_comments_require_whitespace_after_colon():
    cases = (
        ("info:\n", "info:#not-a-comment\n"),
        ("servers:\n", "servers:#not-a-comment\n"),
        ("paths:\n", "paths:#not-a-comment\n"),
        ('  "/safe":\n', '  "/safe":#not-a-comment\n'),
    )
    for valid_header, malformed_header in cases:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            contract = root / "contracts/openapi.yaml"
            contract.parent.mkdir(parents=True)
            source = (
                "info:\n  title: Example API (draft)\n  version: 1.0-draft\n"
                "servers:\n- url: https://api.example.invalid\n"
                "paths:\n  /safe:\n    get:\n      operationId: safe\n"
            )
            if valid_header.startswith('  "'):
                source = source.replace("  /safe:\n", valid_header, 1)
            assert valid_header in source
            source = source.replace(valid_header, malformed_header, 1)
            contract.write_text(source, encoding="utf-8")
            rows = scan_openapi_contract(contract, root)
            assert any(row["unresolved"] for row in rows), malformed_header
            assert main(["--root", str(root), "--check"]) == 1


def test_server_sequence_ends_before_root_scalar_or_inline_value():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        contract = root / "contracts/openapi.yaml"
        contract.parent.mkdir(parents=True)
        contract.write_text(
            "info:\n  title: Example API (draft)\n  version: 1.0-draft\n"
            "servers:\n- url: https://api.example.invalid\n"
            "openapi: 3.1.2\n"
            "paths:\n  /safe:\n    get:\n      operationId: safe\n",
            encoding="utf-8",
        )
        rows = scan_openapi_contract(contract, root)
        assert any(r.get("operation_id") == "safe" and not r["unresolved"] for r in rows)
        assert not any(r["kind"] == "historical-openapi-control" and r["unresolved"] for r in rows)
        assert main(["--root", str(root), "--check"]) == 0


def test_server_list_rejects_multiple_spaces_after_sequence_marker():
    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        contract = root / "contracts/openapi.yaml"
        contract.parent.mkdir(parents=True)
        contract.write_text(
            "info:\n  title: Example API (draft)\n  version: 1.0-draft\n"
            "servers:\n"
            "-  description: placeholder\n"
            "  url: https://api.example.invalid\n"
            "paths:\n  /safe:\n    get:\n      operationId: safe\n",
            encoding="utf-8",
        )
        rows = scan_openapi_contract(contract, root)
        assert any(r["kind"] == "historical-openapi-control" and r["unresolved"] for r in rows)
        assert main(["--root", str(root), "--check"]) == 1
