"""Static legacy route inventory regression checks (#165)."""
from pathlib import Path
from tempfile import TemporaryDirectory

from scripts.inventory_legacy_routes import inventory, main, scan_python


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
