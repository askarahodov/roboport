"""Read-only inventory of FastAPI and React routes before automotive cutover (#165).

This is static source analysis; it does not assert runtime reachability or ACL.
Requires Python 3.13 stdlib only. Does not import the application or connect to DB.
"""
from __future__ import annotations

import argparse
import ast
import json
import re
from pathlib import Path

LEGACY = re.compile(r"(?:robot|ticket|catalog|request-demo|guest|device)", re.I)
ROUTE = re.compile(r"<Route\b[^>]*\bpath\s*=\s*[\"']([^\"']+)[\"']", re.S)
NAV = re.compile(r"<NavLink\b[^>]*\bto\s*=\s*[\"']([^\"']+)[\"']", re.S)
METHODS = {"get", "post", "put", "patch", "delete", "options", "head", "api_route"}


def _literal(node: ast.AST | None) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def scan_python(path: Path, root: Path) -> list[dict[str, object]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    prefixes = {}
    results = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            if isinstance(node.value.func, ast.Name) and node.value.func.id == "APIRouter":
                prefix = next((_literal(k.value) for k in node.value.keywords if k.arg == "prefix"), None)
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        prefixes[target.id] = prefix or ""
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call) or not isinstance(decorator.func, ast.Attribute):
                continue
            owner = decorator.func.value
            if not isinstance(owner, ast.Name) or decorator.func.attr not in METHODS:
                continue
            path_part = _literal(decorator.args[0]) if decorator.args else None
            if path_part is None:
                continue
            full_path = prefixes.get(owner.id, "") + path_part
            hidden = any(k.arg == "include_in_schema" and isinstance(k.value, ast.Constant)
                         and k.value.value is False for k in decorator.keywords)
            results.append({
                "source": str(path.relative_to(root)), "line": node.lineno,
                "kind": "api", "method": decorator.func.attr.upper(),
                "path": full_path, "legacy_candidate": bool(LEGACY.search(full_path)),
                "schema_hidden": hidden,
                "review": "manual-required",
            })
    return results


def inventory(root: Path) -> list[dict[str, object]]:
    rows = []
    api = root / "apps/api/roboport_api"
    for path in sorted(api.glob("*.py")):
        rows.extend(scan_python(path, root))
    web = root / "apps/web/src"
    for path in sorted(web.rglob("*.tsx")):
        content = path.read_text(encoding="utf-8")
        for kind, pattern in (("web-route", ROUTE), ("web-navigation", NAV)):
            for match in pattern.finditer(content):
                url = match.group(1)
                rows.append({
                    "source": str(path.relative_to(root)),
                    "line": content.count("\n", 0, match.start()) + 1,
                    "kind": kind, "method": None, "path": url,
                    "legacy_candidate": bool(LEGACY.search(url)),
                    "schema_hidden": None, "review": "manual-required",
                })
    return sorted(rows, key=lambda r: (str(r["source"]), int(r["line"]), str(r["kind"]), str(r["path"])))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--legacy-only", action="store_true")
    args = parser.parse_args()
    rows = inventory(args.root.resolve())
    if args.legacy_only:
        rows = [row for row in rows if row["legacy_candidate"]]
    print(json.dumps({"source": "static-only", "routes": rows, "count": len(rows)},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
