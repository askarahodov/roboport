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
from urllib.parse import urlsplit

LEGACY = re.compile(r"(?:robot|ticket|catalog|request-demo|guest|device)", re.I)
ROUTE = re.compile(r"<Route\b[^>]*\bpath\s*=\s*[\"']([^\"']+)[\"']", re.S)
NAV = re.compile(r"<NavLink\b[^>]*\bto\s*=\s*[\"']([^\"']+)[\"']", re.S)
METHODS = {"get", "post", "put", "patch", "delete", "options", "head", "api_route"}


MAX_STATIC_PATH = 4096


def _literal(node: ast.AST | None, constants: dict[str, str] | None = None) -> str | None:
    """Resolve only statically proven strings; never execute application code.

    FastAPI routes frequently use PREFIX + "/export" instead of literals.
    An unknown expression must be reported, never silently skipped.
    """
    names = constants or {}
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value if len(node.value) <= MAX_STATIC_PATH else None
    if isinstance(node, ast.Name):
        return names.get(node.id)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _literal(node.left, names), _literal(node.right, names)
        if left is not None and right is not None and len(left) + len(right) <= MAX_STATIC_PATH:
            return left + right
    if isinstance(node, ast.JoinedStr):
        parts = []
        for part in node.values:
            if isinstance(part, ast.FormattedValue):
                if part.conversion not in (-1, 115) or part.format_spec is not None:
                    return None
                value = _literal(part.value, names)
            else:
                value = _literal(part, names)
            if value is None:
                return None
            parts.append(value)
        joined = "".join(parts)
        return joined if len(joined) <= MAX_STATIC_PATH else None
    return None


def _module_strings(tree: ast.Module) -> dict[str, str]:
    """Only safe top-level constants, never mutable or ambiguous bindings.

    A final assignment is not the value observed when an earlier route
    decorator ran. Once re-bound, a name must remain unresolved for the
    entire scan, including when the replacement is another string literal.
    """
    names: dict[str, str] = {}
    seen: set[str] = set()
    unsafe: set[str] = set()

    def bind(name: str, value: str | None) -> None:
        if name in seen:
            unsafe.add(name)
        seen.add(name)
        if name in unsafe or value is None:
            names.pop(name, None)
        else:
            names[name] = value

    def invalidate(name: str) -> None:
        unsafe.add(name)
        seen.add(name)
        names.pop(name, None)

    for node in tree.body:
        if isinstance(node, ast.Assign):
            value = _literal(node.value, names)
            for target in node.targets:
                if isinstance(target, ast.Name):
                    bind(target.id, value)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            bind(node.target.id, _literal(node.value, names))
        elif isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name):
            invalidate(node.target.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            # Definitions also bind global names, even when not strings.
            if node.name in seen:
                invalidate(node.name)
        elif isinstance(node, (ast.If, ast.For, ast.While, ast.Try, ast.With, ast.Match)):
            # Conditional module-level writes have unknown execution order.
            # Conservatively invalidate their targets, but do not walk into
            # function/class bodies which have an independent local scope.
            pending = [node]
            while pending:
                item = pending.pop()
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef,
                                     ast.ClassDef, ast.Lambda)):
                    continue
                if isinstance(item, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                    targets = (item.targets if isinstance(item, ast.Assign)
                               else [item.target])
                    for target in targets:
                        if isinstance(target, ast.Name):
                            invalidate(target.id)
                pending.extend(ast.iter_child_nodes(item))
    return names


def _keyword(call: ast.Call, name: str) -> ast.AST | None:
    return next((item.value for item in call.keywords if item.arg == name), None)


def _router_settings(tree: ast.Module, constants: dict[str, str]) -> dict[str, tuple[str | None, bool]]:
    """Resolve APIRouter prefix plus its include_in_schema default."""
    routers: dict[str, tuple[str | None, bool]] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            value, targets = node.value, node.targets
        elif isinstance(node, ast.AnnAssign):
            value, targets = node.value, [node.target]
        else:
            continue
        if not isinstance(value, ast.Call):
            continue
        constructor = value.func
        if not isinstance(constructor, ast.Name) or constructor.id not in {"APIRouter", "FastAPI"}:
            continue
        prefix_node = _keyword(value, "prefix")
        # A **kwargs expansion can supply an implicit, mutable prefix.
        # It is not evidence that the effective router prefix is empty.
        unknown_kwargs = any(item.arg is None for item in value.keywords)
        prefix = (None if unknown_kwargs else
                  _literal(prefix_node, constants) if prefix_node is not None else "")
        schema_node = _keyword(value, "include_in_schema")
        hidden = isinstance(schema_node, ast.Constant) and schema_node.value is False
        for target in targets:
            if isinstance(target, ast.Name):
                routers[target.id] = (prefix, hidden)
    return routers


def _route_arguments(call: ast.Call) -> ast.AST | None:
    return call.args[0] if call.args else _keyword(call, "path")


def scan_python(path: Path, root: Path) -> list[dict[str, object]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    constants = _module_strings(tree)
    routers = _router_settings(tree, constants)
    results: list[dict[str, object]] = []
    parents = {id(child): parent for parent in ast.walk(tree)
               for child in ast.iter_child_nodes(parent)}

    def nested_scope(node: ast.AST) -> bool:
        """A function/class-local route may shadow a module-level PREFIX."""
        parent = parents.get(id(node))
        while parent is not None and parent is not tree:
            if isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef,
                                   ast.ClassDef, ast.Lambda)):
                return True
            parent = parents.get(id(parent))
        return False

    def record(owner: str, call: ast.Call, method: str, line: int,
               *, local_scope: bool = False) -> None:
        # An imported/factory router is not proof of an empty prefix.
        # Treat unknown owners as unresolved, not as a trusted root router.
        prefix, inherited_hidden = routers.get(owner, (None, False))
        part = _literal(_route_arguments(call), constants)
        resolved = not local_scope and prefix is not None and part is not None
        full_path = prefix + part if resolved else "<unresolved>"
        decorator_hidden = _keyword(call, "include_in_schema")
        hidden = inherited_hidden or (
            isinstance(decorator_hidden, ast.Constant) and decorator_hidden.value is False
        )
        results.append({
            "source": str(path.relative_to(root)), "line": line,
            "kind": "api", "method": method,
            "path": full_path, "legacy_candidate": bool(LEGACY.search(full_path)) if resolved else True,
            "schema_hidden": hidden, "unresolved": not resolved,
            "review": "path-unresolved" if not resolved else "manual-required",
        })

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for decorator in node.decorator_list:
                if (not isinstance(decorator, ast.Call) or
                    not isinstance(decorator.func, ast.Attribute) or
                    decorator.func.attr not in METHODS):
                    continue
                owner = (decorator.func.value.id if isinstance(decorator.func.value, ast.Name)
                         else "<unresolved-router-owner>")
                record(owner, decorator, decorator.func.attr.upper(), node.lineno,
                       local_scope=nested_scope(node))
        # Also inventory imperative registrations. They must not bypass the
        # static cutover check just because they have no route decorator.
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and
            node.func.attr == "add_api_route"):
            # A factory return or attribute receiver is still a possible
            # route registration. Its effective prefix cannot be proved.
            owner = (node.func.value.id if isinstance(node.func.value, ast.Name)
                     else "<unresolved-router-owner>")
            record(owner, node, "API_ROUTE", node.lineno,
                   local_scope=nested_scope(node))
    return results



def scan_openapi_contract(path: Path, root: Path) -> list[dict[str, object]]:
    """Conservatively inventory the historical OpenAPI draft without loading YAML.

    Only the small YAML subset used by this checked-in draft is interpreted.
    Any unrecognized server/path/method structure becomes unresolved so a
    partial parse can never be reported as a safe inventory.
    """
    if not path.is_file():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()

    def scalar(value: str) -> str:
        value = value.split(" #", 1)[0].strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            return value[1:-1]
        return value

    title = next((scalar(m.group(1)) for line in lines
                  if (m := re.match(r"^  title:\s*(.+)$", line))), "")
    version = next((scalar(m.group(1)) for line in lines
                    if (m := re.match(r"^  version:\s*(.+)$", line))), "")

    # Parse every server list item as a mapping, regardless of key order.
    # Unsupported YAML constructs are rejected instead of guessed.
    servers_index = next((i for i, line in enumerate(lines)
                          if re.match(r"^servers:\s*(?:#.*)?$", line)), None)
    server_urls: list[str] = []
    servers_valid = servers_index is not None
    server_entries = 0
    entry_fields: dict[str, str] | None = None
    entry_valid = True

    def finish_server_entry() -> None:
        nonlocal entry_fields, entry_valid, servers_valid
        if entry_fields is None:
            return
        if not entry_valid or set(entry_fields) - {"url", "description"} or "url" not in entry_fields:
            servers_valid = False
        else:
            server_urls.append(entry_fields["url"])
        entry_fields = None
        entry_valid = True

    if servers_index is not None:
        for line in lines[servers_index + 1:]:
            if re.match(r"^[A-Za-z][\w-]*:\s*(?:#.*)?$", line):
                break
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            item = re.match(r"^\s*-\s*(.*)$", line)
            if item:
                finish_server_entry()
                server_entries += 1
                entry_fields = {}
                remainder = item.group(1).strip()
                if remainder:
                    field = re.match(r"^([A-Za-z][\w-]*):\s*(.*?)\s*(?:#.*)?$", remainder)
                    if not field:
                        entry_valid = False
                    else:
                        entry_fields[field.group(1)] = scalar(field.group(2))
                continue
            child = re.match(r"^\s+([A-Za-z][\w-]*):\s*(.*?)\s*(?:#.*)?$", line)
            if child and entry_fields is not None:
                key, value = child.group(1), scalar(child.group(2))
                if key in entry_fields:
                    entry_valid = False
                entry_fields[key] = value
            else:
                servers_valid = False
                if entry_fields is not None:
                    entry_valid = False
        finish_server_entry()
    if server_entries == 0 or not server_urls:
        servers_valid = False

    def placeholder_server(url: str) -> bool:
        try:
            parsed = urlsplit(url)
        except ValueError:
            return False
        return (parsed.scheme == "https" and parsed.hostname is not None
                and parsed.hostname.endswith(".invalid")
                and parsed.username is None and parsed.password is None
                and not parsed.query and not parsed.fragment)

    safe_draft = ("draft" in title.lower() and version.lower().endswith("-draft")
                  and servers_valid and len(server_urls) == server_entries
                  and all(placeholder_server(url) for url in server_urls))

    rows: list[dict[str, object]] = []
    if not safe_draft:
        rows.append({
            "source": str(path.relative_to(root)), "line": 1,
            "kind": "historical-openapi-control", "method": None,
            "path": "contracts/openapi.yaml", "legacy_candidate": True,
            "schema_hidden": None, "unresolved": True,
            "review": "draft-marker-or-server-inventory-invalid",
        })

    start = next((i for i, line in enumerate(lines) if line == "paths:"), None)
    if start is None:
        rows.append({
            "source": str(path.relative_to(root)), "line": 1,
            "kind": "historical-openapi-control", "method": None,
            "path": "<paths-section-missing>", "legacy_candidate": True,
            "schema_hidden": None, "unresolved": True,
            "review": "paths-section-missing",
        })
        return rows

    current_path: str | None = None
    path_line = 0
    methods: list[dict[str, object]] = []

    def unresolved_path(line_no: int, review: str, path_value: str = "<unresolved-path>") -> None:
        rows.append({
            "source": str(path.relative_to(root)), "line": line_no,
            "kind": "historical-openapi-path", "method": None,
            "path": path_value, "legacy_candidate": True,
            "schema_hidden": None, "unresolved": True, "review": review,
        })

    def flush() -> None:
        nonlocal methods
        if current_path is None:
            return
        if not methods:
            unresolved_path(path_line, "path-has-no-recognized-method", current_path)
        for item in methods:
            operation_id = item["operation_id"]
            rows.append({
                "source": str(path.relative_to(root)), "line": item["line"],
                "kind": "historical-openapi-draft", "method": item["method"],
                "path": current_path,
                "legacy_candidate": bool(LEGACY.search(current_path)),
                "schema_hidden": None, "unresolved": operation_id is None,
                "operation_id": operation_id,
                "contract_status": "historical-draft-not-runtime",
                "review": "operation-id-missing" if operation_id is None else "manual-disposition-required",
            })
        methods = []

    current_method: dict[str, object] | None = None
    for index in range(start + 1, len(lines)):
        line = lines[index]
        if line and not line.startswith((" ", "\t", "#")):
            break
        if not line.strip() or line.lstrip().startswith("#"):
            continue

        # Path-item keys are exactly two spaces deep. Quoted keys and comments
        # are supported; every other mapping key is an explicit parse failure.
        path_match = re.match(r"""^  (?:"([^"]+)"|'([^']+)'|(/[^:]+)):\s*(?:#.*)?$""", line)
        if path_match:
            flush()
            current_method = None
            current_path = next(group for group in path_match.groups() if group is not None)
            path_line = index + 1
            if not current_path.startswith("/"):
                unresolved_path(path_line, "path-key-is-not-an-absolute-path", current_path)
                current_path = None
            continue
        if re.match(r"^  \S.*:\s*(?:#.*)?$", line):
            flush()
            current_path = None
            current_method = None
            unresolved_path(index + 1, "unrecognized-path-key")
            continue

        method_key = re.match(r"^    ([A-Za-z_$][\w$-]*):\s*(?:#.*)?$", line)
        if method_key and current_path is not None:
            key = method_key.group(1).lower()
            if key in {"get", "post", "put", "patch", "delete", "options", "head", "trace"}:
                methods.append({"method": key.upper(), "operation_id": None, "line": index + 1})
                current_method = methods[-1]
            elif key not in {"parameters", "servers", "summary", "description", "$ref"} and not key.startswith("x-"):
                unresolved_path(index + 1, "unrecognized-path-item-key", current_path)
                current_method = None
            else:
                current_method = None
            continue
        if re.match(r"^    \S.*:\s*(?:#.*)?$", line) and current_path is not None:
            unresolved_path(index + 1, "unrecognized-path-item-key", current_path)
            current_method = None
            continue

        operation_match = re.match(r"^      operationId:\s*([^\s#]+)\s*(?:#.*)?$", line)
        if operation_match:
            if current_method is None or current_method["operation_id"] is not None:
                unresolved_path(index + 1, "operation-id-without-current-method", current_path or "<unresolved-path>")
            else:
                current_method["operation_id"] = scalar(operation_match.group(1))
            continue
        # A method-shaped key with trailing comments/spacing must not be
        # silently attached to the preceding operation.
        if re.match(r"^    [A-Za-z_$][\w$-]*\s+:", line) and current_path is not None:
            unresolved_path(index + 1, "malformed-method-key", current_path)
            current_method = None

    flush()
    if not any(row["kind"] == "historical-openapi-draft" for row in rows):
        unresolved_path(start + 1, "no-operation-paths")
    seen: set[tuple[str, str]] = set()
    seen_operation_ids: set[str] = set()
    for row in rows:
        if row["kind"] != "historical-openapi-draft":
            continue
        key = (str(row["path"]), str(row["method"]))
        if key in seen:
            row["unresolved"] = True
            row["review"] = "duplicate-path-method"
        seen.add(key)
        operation_id = row.get("operation_id")
        if isinstance(operation_id, str):
            if operation_id in seen_operation_ids:
                row["unresolved"] = True
                row["review"] = "duplicate-operation-id"
            seen_operation_ids.add(operation_id)
    return rows

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
    rows.extend(scan_openapi_contract(root / "contracts/openapi.yaml", root))
    return sorted(rows, key=lambda r: (str(r["source"]), int(r["line"]), str(r["kind"]), str(r["path"])))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--legacy-only", action="store_true")
    parser.add_argument("--check", action="store_true",
                        help="Fail closed if any Python API path cannot be resolved")
    args = parser.parse_args(argv)
    rows = inventory(args.root.resolve())
    unresolved = [row for row in rows if row.get("unresolved")]
    if args.check:
        # CI should see paths needing manual inspection, not a false clean
        # inventory; no full source expressions or application data logged.
        print(json.dumps({
            "source": "static-only", "apiPathsResolved": not unresolved,
            "routeCount": len(rows),
            "historicalOpenApiPaths": sum(r["kind"] == "historical-openapi-draft" for r in rows),
            "historicalOpenApiDraftSafe": not any(r["kind"] == "historical-openapi-control" or r["kind"] == "historical-openapi-path" or (r["kind"] == "historical-openapi-draft" and r["unresolved"]) for r in rows),
            "unresolved": [{"source": r["source"], "line": r["line"]}
                           for r in unresolved],
        }, ensure_ascii=False))
        return 1 if unresolved else 0
    if args.legacy_only:
        rows = [row for row in rows if row["legacy_candidate"]]
    print(json.dumps({"source": "static-only", "routes": rows, "count": len(rows)},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
