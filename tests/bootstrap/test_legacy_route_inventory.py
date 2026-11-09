"""Static legacy route inventory regression checks (#165)."""
from pathlib import Path
from tempfile import TemporaryDirectory

from scripts.inventory_legacy_routes import inventory, scan_python


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
