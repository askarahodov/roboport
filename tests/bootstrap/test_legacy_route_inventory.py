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
