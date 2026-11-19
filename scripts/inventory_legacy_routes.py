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

    Only a deliberately small YAML subset is interpreted. Unsupported nesting,
    escapes, overrides, aliases and malformed mappings become unresolved so a
    partial parse can never be reported as a safe inventory.
    """
    if not path.is_file():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()

    def without_comment(value: str) -> str:
        quote: str | None = None
        for index, char in enumerate(value):
            if quote is not None:
                if char == quote:
                    quote = None
            elif char in "\"'":
                quote = char
            elif char == "#" and index > 0 and value[index - 1].isspace():
                return value[:index].rstrip()
        return value.strip()

    def empty_mapping_tail(value: str) -> bool:
        # A YAML comment after a mapping colon needs separation whitespace.
        # key:#text is not an empty mapping value.
        return value == "" or (
            value[0] in " \t" and not without_comment(value).strip()
        )

    def string_scalar(value: str) -> str | None:
        """Read only plain/quoted YAML strings; reject other scalar types and escapes."""
        raw = without_comment(value).strip()
        if not raw:
            return None
        if raw[0] in "\"'":
            quote = raw[0]
            if len(raw) < 2 or raw[-1] != quote:
                return None
            inner = raw[1:-1]
            # Escapes and embedded quote escaping are deliberately unsupported.
            if "\\" in inner or quote in inner:
                return None
            return inner
        if (raw[0] in "[{|>&*!" or raw in {
                "~", "null", "Null", "NULL", "true", "True", "TRUE",
                "false", "False", "FALSE",
            } or re.fullmatch(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?", raw)
                or re.search(r":(?:\s|$)", raw)
                or re.match(r"^[-?]\s", raw)):
            return None
        return raw

    info_declarations = [
        (i, m.group(1)) for i, line in enumerate(lines)
        if (m := re.match(r"^info\s*:(.*)$", line))
    ]
    info_indexes = [
        i for i, tail in info_declarations if empty_mapping_tail(tail)
    ]
    info_valid = len(info_declarations) == 1 and len(info_indexes) == 1
    title_values: list[str | None] = []
    version_values: list[str | None] = []
    if info_valid:
        for line in lines[info_indexes[0] + 1:]:
            if line and not line.startswith((" ", "\t", "#")):
                break
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            if re.match(r"""^  (?:"[^"]*"|'[^']*'|<<)\s*:""", line):
                info_valid = False
                continue
            field = re.match(r"^  ([A-Za-z][\w-]*):(?:[ \t]+(.*))?$", line)
            if not field:
                if line.startswith("  ") and not line.startswith("   "):
                    info_valid = False
                continue
            key, raw_value = field.group(1), field.group(2) or ""
            if key == "title":
                title_values.append(string_scalar(raw_value))
            elif key == "version":
                version_values.append(string_scalar(raw_value))
    info_valid = info_valid and len(title_values) == 1 and len(version_values) == 1
    title = title_values[0] if len(title_values) == 1 else None
    version = version_values[0] if len(version_values) == 1 else None

    # Parse every root server list item as a mapping. Child fields must be
    # exactly two spaces deeper than their list item; nested YAML is unsupported.
    # Count every root-level spelling of the reserved key, including quoted
    # keys and anchored/inline values. A merge key can inject servers too.
    server_declarations = [i for i, line in enumerate(lines)
                           if re.match(r"""^(?:servers|"servers"|'servers')\s*:""", line)]
    merge_declarations = [i for i, line in enumerate(lines)
                          if re.match(r"^<<\s*:", line)]
    unsupported_root_mapping = any(
        re.match(r"^(?:---|\.\.\.)(?:\s|$)", line)
        or line.startswith("%")
        or re.match(
            r"""^(?:\s*\?|[:&!*]|(?:"[^"]*"|'[^']*'|<<))""",
            line,
        )
        for line in lines
    )
    servers_indexes = [i for i, line in enumerate(lines)
                       if re.match(r"^servers:(?:[ \t]+(?:#.*)?)?$", line)]
    server_urls: list[str] = []
    servers_valid = (len(server_declarations) == 1
                     and server_declarations == servers_indexes
                     and not merge_declarations
                     and not unsupported_root_mapping)
    server_entries = 0
    sequence_indent: int | None = None
    entry_indent: int | None = None
    entry_fields: dict[str, str] | None = None
    entry_valid = True

    def add_server_field(key: str, raw_value: str | None) -> None:
        nonlocal entry_valid
        if entry_fields is None or key not in {"url", "description"} or key in entry_fields:
            entry_valid = False
            return
        value = string_scalar(raw_value or "")
        if value is None:
            entry_valid = False
            return
        entry_fields[key] = value

    def finish_server_entry() -> None:
        nonlocal entry_fields, entry_valid, entry_indent, servers_valid
        if entry_fields is None:
            return
        if (not entry_valid or set(entry_fields) - {"url", "description"}
                or "url" not in entry_fields):
            servers_valid = False
        else:
            server_urls.append(entry_fields["url"])
        entry_fields = None
        entry_indent = None
        entry_valid = True

    if servers_valid:
        servers_index = servers_indexes[0]
        for line in lines[servers_index + 1:]:
            if re.match(r"^[A-Za-z][\w-]*:\s*(?:#.*)?$", line):
                break
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            # YAML sequence markers require whitespace (or end of line).
            # "-url:" is a mapping key, not a list item.
            item = re.match(r"^( *)-(?:[ \t]+(.*))?$", line)
            if item:
                finish_server_entry()
                item_indent = len(item.group(1))
                if sequence_indent is None:
                    sequence_indent = item_indent
                elif item_indent != sequence_indent:
                    servers_valid = False
                server_entries += 1
                entry_indent = item_indent
                entry_fields = {}
                remainder = (item.group(2) or "").strip()
                if remainder:
                    field = re.match(r"^([A-Za-z][\w-]*):(?:[ \t]+(.*))?$", remainder)
                    if field:
                        add_server_field(field.group(1), field.group(2))
                    else:
                        entry_valid = False
                continue
            if entry_fields is not None and entry_indent is not None:
                child_pattern = rf"^ {{{entry_indent + 2}}}([A-Za-z][\w-]*):(?:[ \t]+(.*))?$"
                child = re.match(child_pattern, line)
                if child:
                    add_server_field(child.group(1), child.group(2))
                else:
                    servers_valid = False
                    entry_valid = False
            else:
                servers_valid = False
        finish_server_entry()
    if server_entries == 0 or not server_urls or len(server_urls) != server_entries:
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

    safe_draft = (info_valid and title is not None and "draft" in title.lower()
                  and version is not None and version.lower().endswith("-draft")
                  and servers_valid and all(placeholder_server(url) for url in server_urls))

    rows: list[dict[str, object]] = []
    if not safe_draft:
        rows.append({
            "source": str(path.relative_to(root)), "line": 1,
            "kind": "historical-openapi-control", "method": None,
            "path": "contracts/openapi.yaml", "legacy_candidate": True,
            "schema_hidden": None, "unresolved": True,
            "review": "draft-marker-or-server-inventory-invalid",
        })

    if unsupported_root_mapping:
        rows.append({
            "source": str(path.relative_to(root)), "line": 1,
            "kind": "historical-openapi-control", "method": None,
            "path": "contracts/openapi.yaml", "legacy_candidate": True,
            "schema_hidden": None, "unresolved": True,
            "review": "unsupported-root-yaml-key",
        })
    webhooks_declarations = [
        i for i, line in enumerate(lines)
        if re.match(r"^webhooks\s*:", line)
    ]
    if webhooks_declarations:
        rows.append({
            "source": str(path.relative_to(root)), "line": webhooks_declarations[0] + 1,
            "kind": "historical-openapi-control", "method": None,
            "path": "<webhooks-section-unreviewed>", "legacy_candidate": True,
            "schema_hidden": None, "unresolved": True,
            "review": "webhooks-section-unreviewed",
        })

    path_declarations = [
        (i, m.group(1)) for i, line in enumerate(lines)
        if (m := re.match(r"^paths\s*:(.*)$", line))
    ]
    paths_indexes = [
        i for i, tail in path_declarations if empty_mapping_tail(tail)
    ]
    if len(path_declarations) != 1 or len(paths_indexes) != 1:
        review = (
            "paths-section-missing" if not path_declarations
            else "paths-section-duplicate" if len(path_declarations) > 1
            else "paths-section-unsupported-value"
        )
        rows.append({
            "source": str(path.relative_to(root)), "line": 1,
            "kind": "historical-openapi-control", "method": None,
            "path": "<paths-section-unresolved>", "legacy_candidate": True,
            "schema_hidden": None, "unresolved": True,
            "review": review,
        })
        if not paths_indexes:
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
            methods = []
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

    if len(paths_indexes) > 1:
        unresolved_path(paths_indexes[1] + 1, "duplicate-paths-section", "<paths-section-duplicate>")

    current_method: dict[str, object] | None = None
    seen_paths: set[str] = set()
    for index in range(paths_indexes[0] + 1, len(lines)):
        line = lines[index]
        if line and not line.startswith((" ", "\t", "#")):
            break
        if not line.strip() or line.lstrip().startswith("#"):
            continue

        indent = len(line) - len(line.lstrip(" "))
        if "\t" in line[:indent] or "\t" in line:
            unresolved_path(index + 1, "tabs-in-path-section", current_path or "<unresolved-path>")
            current_method = None
            continue
        if indent % 2:
            unresolved_path(index + 1, "invalid-path-section-indentation", current_path or "<unresolved-path>")
            current_method = None
            continue

        if indent == 2:
            path_match = re.match(
                r"""^  (?:"([^"]*)"|'([^']*)'|(/[^:]+)):(?:[ \t]+(?:#.*)?)?$""", line
            )
            if path_match:
                flush()
                current_method = None
                current_path = next(group for group in path_match.groups() if group is not None)
                path_line = index + 1
                if "\\" in current_path:
                    unresolved_path(path_line, "path-key-contains-unsupported-escape", current_path)
                    current_path = None
                elif "%" in current_path:
                    unresolved_path(path_line, "path-key-percent-encoding-unreviewed", current_path)
                    current_path = None
                elif (not current_path.startswith("/") or "#" in current_path
                      or "?" in current_path or any(char.isspace() for char in current_path)):
                    unresolved_path(path_line, "path-key-is-not-an-absolute-path", current_path)
                    current_path = None
                elif current_path in seen_paths:
                    unresolved_path(path_line, "duplicate-path-key", current_path)
                if current_path is not None:
                    seen_paths.add(current_path)
                continue
            flush()
            current_path = None
            current_method = None
            unresolved_path(index + 1, "unrecognized-path-key")
            continue

        if indent == 4:
            if current_path is None:
                unresolved_path(index + 1, "path-item-without-path")
                current_method = None
                continue
            malformed_method = re.match(r"^    [A-Za-z_$][\w$-]*\s+:", line)
            if malformed_method:
                unresolved_path(index + 1, "malformed-method-key", current_path)
                current_method = None
                continue
            method_key = re.match(r"^    ([A-Za-z_$][\w$-]*):(?:[ \t]+(.*))?$", line)
            if not method_key:
                unresolved_path(index + 1, "malformed-path-item-key", current_path)
                current_method = None
                continue
            key = method_key.group(1)
            value = without_comment(method_key.group(2) or "").strip()
            if key in {"get", "post", "put", "patch", "delete", "options", "head", "trace"}:
                if value:
                    unresolved_path(index + 1, "unsupported-inline-method-value", current_path)
                    current_method = None
                else:
                    methods.append({"method": key.upper(), "operation_id": None, "line": index + 1})
                    current_method = methods[-1]
            elif key in {"servers", "$ref"}:
                unresolved_path(
                    index + 1,
                    "path-item-server-override-unreviewed" if key == "servers" else "path-item-reference-unreviewed",
                    current_path,
                )
                current_method = None
            elif key in {"parameters", "summary", "description"} or key.startswith("x-"):
                current_method = None
            else:
                unresolved_path(index + 1, "unrecognized-path-item-key", current_path)
                current_method = None
            continue

        if indent == 6:
            # Sequence members of already-recognized operation properties
            # (tags, parameters, x-extensions) share this indentation.
            if re.match(r"^      -(?:[ \\t]+|$)", line):
                continue
            operation_key = re.match(
                r"""^      (?:"([^"]*)"|'([^']*)'|([A-Za-z_$][\w$-]*|<<)):(?:[ \t]+(.*))?$""",
                line,
            )
            if operation_key is None:
                unresolved_path(index + 1, "malformed-operation-key", current_path or "<unresolved-path>")
                current_method = None
                continue
            quoted_key = operation_key.group(1) is not None or operation_key.group(2) is not None
            key = operation_key.group(1) or operation_key.group(2) or operation_key.group(3)
            value = operation_key.group(4) or ""
            if quoted_key:
                unresolved_path(index + 1, "quoted-operation-key-unreviewed", current_path or "<unresolved-path>")
                current_method = None
                continue
            if key == "<<":
                unresolved_path(index + 1, "operation-merge-key-unreviewed", current_path or "<unresolved-path>")
                current_method = None
                continue
            if key == "servers":
                unresolved_path(index + 1, "operation-server-override-unreviewed", current_path or "<unresolved-path>")
                current_method = None
                continue
            if key == "$ref":
                unresolved_path(index + 1, "operation-reference-unreviewed", current_path or "<unresolved-path>")
                current_method = None
                continue
            if key == "operationId":
                operation_id = string_scalar(value)
                if current_method is None or current_method["operation_id"] is not None or operation_id is None:
                    unresolved_path(index + 1, "invalid-or-duplicate-operation-id", current_path or "<unresolved-path>")
                else:
                    current_method["operation_id"] = operation_id
                continue
            if key == "callbacks":
                # Callback Objects contain nested Path Items and can hide their
                # own server overrides; they are outside this bounded parser.
                unresolved_path(index + 1, "operation-callbacks-unreviewed", current_path or "<unresolved-path>")
                current_method = None
                continue
            if key in {"tags", "summary", "description", "externalDocs", "parameters",
                       "requestBody", "responses", "deprecated", "security"} or key.startswith("x-"):
                continue
            unresolved_path(index + 1, "unrecognized-operation-key", current_path or "<unresolved-path>")
            current_method = None
            continue

    flush()
    if not any(row["kind"] == "historical-openapi-draft" for row in rows):
        unresolved_path(paths_indexes[0] + 1, "no-operation-paths")
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
