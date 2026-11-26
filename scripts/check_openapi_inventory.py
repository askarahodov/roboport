"""Repository-wide OpenAPI inventory and conservative compatibility gate (#119).

No network access: only the already fetched base Git object and current files.
Private synthetic contracts must remain private; never authorize production.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import re
import subprocess

import yaml
from openapi_spec_validator import validate_spec

from scripts.check_openapi_compatibility import HTTP_METHODS, regressions

NAME = re.compile(r"contracts/(?:openapi\.yaml|auto-[a-z0-9-]+-openapi\.(?:json|yaml|yml))")
SHA = re.compile(r"[0-9a-f]{40}")
REQUIRED = {"contracts/openapi.yaml", "contracts/auto-dev-openapi.yaml"}
ROOT = Path(__file__).resolve().parents[1]


class UniqueLoader(yaml.SafeLoader):
    """Reject ambiguous duplicate mapping keys instead of silently taking the last."""


def _unique_mapping(loader, node):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=True)
        if key in result:
            raise ValueError("duplicate mapping key")
        result[key] = loader.construct_object(value_node, deep=True)
    return result


UniqueLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _unique_mapping,
)


def _walk(value, active=None):
    """Reject cyclic aliases and external references before schema validation."""
    if not isinstance(value, (dict, list)):
        return
    active = set() if active is None else active
    if id(value) in active:
        raise ValueError("cyclic alias")
    active.add(id(value))
    try:
        if isinstance(value, dict):
            if any(key in value for key in ("$id", "$dynamicRef", "$recursiveRef")):
                raise ValueError("unsupported reference scope")
            if "$ref" in value and (
                not isinstance(value["$ref"], str) or
                not value["$ref"].startswith("#/")
            ):
                raise ValueError("external or unsupported reference")
            children = value.values()
        else:
            children = value
        for child in children:
            _walk(child, active)
    finally:
        active.remove(id(value))


def load_contract(raw: str) -> dict:
    doc = yaml.load(raw, Loader=UniqueLoader)
    if not isinstance(doc, dict):
        raise ValueError("invalid contract")
    _walk(doc)
    validate_spec(doc)
    return doc


def current_contracts(root: Path) -> dict[str, str]:
    files = {}
    for path in sorted((root / "contracts").iterdir()):
        name = path.relative_to(root).as_posix()
        if NAME.fullmatch(name):
            if path.is_symlink() or not path.is_file():
                raise ValueError("invalid contract file")
            files[name] = path.read_text(encoding="utf-8")
    return files


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        encoding="utf-8",
    ).stdout


def base_contracts(root: Path, sha: str) -> dict[str, str]:
    if not SHA.fullmatch(sha):
        raise ValueError("invalid base sha")
    entries = _git(root, "ls-tree", "-r", "--name-only", "-z", sha, "--", "contracts")
    return {name: _git(root, "show", f"{sha}:{name}")
            for name in entries.split("\0") if NAME.fullmatch(name)}


def _loopback(servers):
    return (type(servers) is list and len(servers) == 1 and
            isinstance(servers[0], dict) and
            servers[0].get("url") == "https://127.0.0.1" and
            "variables" not in servers[0])


def private_boundary(doc: dict) -> list[str]:
    errors = []
    if doc.get("x-synthetic-only") is not True or not _loopback(doc.get("servers")):
        errors.append("PRIVATE_ROOT_BOUNDARY")
    if "webhooks" in doc:
        errors.append("PRIVATE_UNREVIEWED_WEBHOOKS")
    scheme = doc.get("components", {}).get("securitySchemes", {}).get("localSession", {})
    if not isinstance(scheme, dict) or any(scheme.get(k) != v for k, v in
            {"type": "apiKey", "in": "cookie", "name": "rp_session"}.items()):
        errors.append("PRIVATE_SESSION_SCHEME")
    for path, item in doc["paths"].items():
        if not path.startswith("/api/v1/dev/auto/") or "$ref" in item:
            errors.append(f"PRIVATE_PATH_BOUNDARY:{path}")
        if "servers" in item and not _loopback(item["servers"]):
            errors.append(f"PRIVATE_PATH_SERVER:{path}")
        for verb in sorted(HTTP_METHODS.intersection(item)):
            op = item[verb]
            if "servers" in op and not _loopback(op["servers"]):
                errors.append(f"PRIVATE_OPERATION_SERVER:{verb}:{path}")
            if op.get("security", doc.get("security")) != [{"localSession": []}]:
                errors.append(f"PRIVATE_OPERATION_SECURITY:{verb}:{path}")
            if "callbacks" in op:
                errors.append(f"PRIVATE_UNREVIEWED_CALLBACK:{verb}:{path}")
    return errors


def _schema_changes(old, new, at):
    """Conservative recursive checks for named and inline schema shapes."""
    if not isinstance(old, dict) or not isinstance(new, dict):
        return [] if old == new else [f"CHANGED_SCHEMA_SHAPE:{at}"]
    errors = []
    for key in ("type", "$ref"):
        if old.get(key) != new.get(key):
            errors.append(f"CHANGED_SCHEMA_{key}:{at}")
    if set(new.get("required", [])) - set(old.get("required", [])):
        errors.append(f"NEW_REQUIRED_SCHEMA_PROPERTY:{at}")
    old_props, new_props = old.get("properties", {}), new.get("properties", {})
    for key in old_props.keys() - new_props.keys():
        errors.append(f"REMOVED_NESTED_PROPERTY:{at}.{key}")
    for key in old_props.keys() & new_props.keys():
        errors.extend(_schema_changes(old_props[key], new_props[key], at + "." + key))
    for key in ("items", "additionalProperties", "not", "if", "then", "else"):
        if key in old and key not in new:
            errors.append(f"REMOVED_SCHEMA_CONSTRAINT:{at}.{key}")
        elif key in old and key in new:
            errors.extend(_schema_changes(old[key], new[key], at + "." + key))
        elif key in new and key in ("additionalProperties", "not", "if", "then", "else"):
            if new[key] is not True:
                errors.append(f"ADDED_SCHEMA_CONSTRAINT:{at}.{key}")
    for key in ("allOf", "anyOf", "oneOf", "prefixItems"):
        if key in old or key in new:
            # Alternatives are order-sensitive here deliberately: review required.
            left, right = old.get(key, []), new.get(key, [])
            if len(left) != len(right):
                errors.append(f"CHANGED_SCHEMA_ALTERNATIVES:{at}.{key}")
            else:
                for index, (x, y) in enumerate(zip(left, right)):
                    errors.extend(_schema_changes(x, y, f"{at}.{key}[{index}]"))
    # Enum/const or bound changes may affect request and response differently.
    # Block changes conservatively; do not guess which side is compatible.
    for key in ("enum", "const", "format", "pattern", "minimum", "maximum",
                "exclusiveMinimum", "exclusiveMaximum", "minLength", "maxLength",
                "minItems", "maxItems", "uniqueItems", "multipleOf"):
        if old.get(key) != new.get(key):
            errors.append(f"CHANGED_SCHEMA_CONSTRAINT:{at}.{key}")
    return errors


def _content_changes(old, new, at):
    if not isinstance(old, dict) or not isinstance(new, dict):
        return [f"INVALID_CONTENT:{at}"]
    errors = [f"REMOVED_MEDIA_TYPE:{at}:{name}" for name in old.keys() - new.keys()]
    for name in old.keys() & new.keys():
        a, b = old[name], new[name]
        errors.extend(_schema_changes(a.get("schema", {}), b.get("schema", {}),
                                      f"{at}:{name}"))
    return errors


def _parameter_changes(old, new, at):
    errors = []
    for key in ("$ref", "required", "style", "explode", "allowReserved"):
        if old.get(key) != new.get(key):
            errors.append(f"CHANGED_PARAMETER_CONTRACT:{at}:{key}")
    errors.extend(_schema_changes(old.get("schema", {}), new.get("schema", {}), at))
    errors.extend(_content_changes(old.get("content", {}), new.get("content", {}), at))
    return errors


def _resolve_component(doc, entry, kind):
    """Resolve local Reference Objects with exact JSON Pointer decoding.

    Only named components of the expected kind are supported; malformed,
    missing or cyclic references fail closed at the inventory boundary.
    Reference Object description/summary do not override the wire contract.
    """
    prefix = f"#/components/{kind}/"
    seen = set()
    while "$ref" in entry:
        ref = entry["$ref"]
        if (not isinstance(ref, str) or not ref.startswith(prefix)
                or ref in seen):
            raise ValueError("unsupported or cyclic component reference")
        token = ref[len(prefix):]
        if not token or "/" in token or re.search(r"~(?![01])", token):
            raise ValueError("unsupported component pointer")
        # Component names are constrained by OpenAPI; reject percent-encoding
        # rather than interpreting a different pointer from the validator.
        if "%" in token:
            raise ValueError("unsupported encoded component pointer")
        seen.add(ref)
        entry = doc["components"][kind][token.replace("~1", "/").replace("~0", "~")]
        if not isinstance(entry, dict):
            raise ValueError("invalid component")
    return entry


def _parameters(doc, path, op):
    # Operation parameters override path parameters by effective (in, name),
    # regardless of whether either declaration uses an inline or named object.
    result = {}
    for level in (path, op):
        local = {}
        for entry in level.get("parameters", []):
            effective = _resolve_component(doc, entry, "parameters")
            key = (effective["in"], effective["name"])
            if key in local:
                raise ValueError("duplicate effective parameter")
            local[key] = effective
        result.update(local)
    return result


def _header_changes(old_doc, new_doc, old_headers, new_headers, at):
    # HTTP header names are case-insensitive; reject ambiguous duplicates.
    def normalized(headers):
        result = {}
        for name, entry in headers.items():
            key = name.lower()
            if key in result:
                raise ValueError("duplicate response header")
            result[key] = (name, entry)
        return result

    a, b = normalized(old_headers), normalized(new_headers)
    errors = [f"REMOVED_RESPONSE_HEADER:{at}:{a[key][0]}"
              for key in a.keys() - b.keys()]
    for key in a.keys() & b.keys():
        old_name, old_entry = a[key]
        _, new_entry = b[key]
        location = f"{at}:{old_name}"
        errors.extend(_parameter_changes(old_entry, new_entry, location))
        errors.extend(_parameter_changes(
            _resolve_component(old_doc, old_entry, "headers"),
            _resolve_component(new_doc, new_entry, "headers"), location))
    return errors


def _named_changes(old, new):
    errors = []
    left, right = old.get("components", {}), new.get("components", {})
    for kind in ("securitySchemes", "parameters", "headers", "requestBodies", "responses"):
        a, b = left.get(kind, {}), right.get(kind, {})
        for name in a.keys() - b.keys():
            errors.append(f"REMOVED_COMPONENT:{kind}:{name}")
        for name in a.keys() & b.keys():
            at = f"{kind}:{name}"
            if kind == "securitySchemes":
                # Description-only changes do not change the security mechanism.
                if ({k: v for k, v in a[name].items() if k != "description"} !=
                        {k: v for k, v in b[name].items() if k != "description"}):
                    errors.append(f"CHANGED_SECURITY_SCHEME:{name}")
            elif kind in ("parameters", "headers"):
                errors.extend(_parameter_changes(a[name], b[name], at))
            else:
                # These named objects may be used by multiple operations.
                if a[name].get("$ref") != b[name].get("$ref"):
                    errors.append(f"CHANGED_COMPONENT_REFERENCE:{at}")
                if b[name].get("required") is True and a[name].get("required") is not True:
                    errors.append(f"NEW_REQUIRED_COMPONENT:{at}")
                errors.extend(_content_changes(a[name].get("content", {}),
                                               b[name].get("content", {}), at))
                errors.extend(_header_changes(old, new, a[name].get("headers", {}),
                                               b[name].get("headers", {}), at))
    return errors


def deep_changes(old, new):
    errors = regressions(old, new) + _named_changes(old, new)
    a = old.get("components", {}).get("schemas", {})
    b = new.get("components", {}).get("schemas", {})
    for name in a.keys() & b.keys():
        errors.extend(_schema_changes(a[name], b[name], name))
    for path in old["paths"].keys() & new["paths"].keys():
        left, right = old["paths"][path], new["paths"][path]
        for verb in HTTP_METHODS.intersection(left, right):
            a, b = left[verb], right[verb]
            at = verb.upper() + ":" + path
            if a.get("operationId") != b.get("operationId"):
                errors.append(f"CHANGED_OPERATION_ID:{at}")
            if a.get("security", old.get("security")) != b.get("security", new.get("security")):
                errors.append(f"CHANGED_SECURITY:{at}")
            params_a, params_b = _parameters(old, left, a), _parameters(new, right, b)
            for key in params_b.keys() - params_a.keys():
                if params_b[key].get("required") is True:
                    errors.append(f"NEW_REQUIRED_EFFECTIVE_PARAMETER:{at}:{key}")
            for key in params_a.keys() - params_b.keys():
                errors.append(f"REMOVED_EFFECTIVE_PARAMETER:{at}:{key}")
            for key in params_a.keys() & params_b.keys():
                errors.extend(_parameter_changes(params_a[key], params_b[key], f"{at}:{key}"))
            raw_a, raw_b = a.get("requestBody", {}), b.get("requestBody", {})
            body_a = _resolve_component(old, raw_a, "requestBodies")
            body_b = _resolve_component(new, raw_b, "requestBodies")
            if body_b.get("required") is True and body_a.get("required") is not True:
                errors.append(f"NEW_REQUIRED_BODY:{at}")
            if "requestBody" in a and "requestBody" not in b:
                errors.append(f"REMOVED_REQUEST_BODY:{at}")
            if raw_a.get("$ref") != raw_b.get("$ref"):
                errors.append(f"CHANGED_BODY_REFERENCE:{at}")
            errors.extend(_content_changes(body_a.get("content", {}), body_b.get("content", {}), at))
            for code in a.get("responses", {}).keys() & b.get("responses", {}).keys():
                raw_ra, raw_rb = a["responses"][code], b["responses"][code]
                ra = _resolve_component(old, raw_ra, "responses")
                rb = _resolve_component(new, raw_rb, "responses")
                if raw_ra.get("$ref") != raw_rb.get("$ref"):
                    errors.append(f"CHANGED_RESPONSE_REFERENCE:{at}:{code}")
                errors.extend(_content_changes(ra.get("content", {}), rb.get("content", {}),
                                               f"{at}:{code}"))
                errors.extend(_header_changes(old, new, ra.get("headers", {}),
                                               rb.get("headers", {}), f"{at}:{code}"))
    return errors


def _verify_effective_components(doc):
    for item in doc["paths"].values():
        for verb in HTTP_METHODS.intersection(item):
            op = item[verb]
            _parameters(doc, item, op)
            if "requestBody" in op:
                _resolve_component(doc, op["requestBody"], "requestBodies")
            for response in op.get("responses", {}).values():
                resolved = _resolve_component(doc, response, "responses")
                _header_changes(doc, doc, resolved.get("headers", {}),
                                resolved.get("headers", {}), "response")


def check_inventory(current: dict[str, str], previous: dict[str, str] | None = None):
    """Return deterministic reasons; no raw spec/exception content printed."""
    errors = []
    for name in sorted(REQUIRED - current.keys()):
        errors.append(f"MISSING_REQUIRED_CONTRACT:{name}")
    if previous is not None:
        for name in sorted(previous.keys() - current.keys()):
            errors.append(f"REMOVED_CONTRACT:{name}")
    for name, raw in sorted(current.items()):
        try:
            doc = load_contract(raw)
            _verify_effective_components(doc)
            if name != "contracts/openapi.yaml":
                errors.extend(f"{name}:{reason}" for reason in private_boundary(doc))
            if previous is not None and name in previous and previous[name] != raw:
                old = load_contract(previous[name])
                errors.extend(f"{name}:{reason}" for reason in deep_changes(old, doc))
        except Exception:
            # Validator messages can include untrusted document content.
            errors.append(f"INVALID_OR_UNSUPPORTED_CONTRACT:{name}")
    return sorted(set(errors))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-sha")
    args = parser.parse_args(argv)
    try:
        current = current_contracts(ROOT)
        previous = base_contracts(ROOT, args.base_sha) if args.base_sha else None
        errors = check_inventory(current, previous)
    except (OSError, ValueError, subprocess.SubprocessError):
        errors = ["CONTRACT_INVENTORY_UNAVAILABLE"]
    if errors:
        for error in errors:
            print("BLOCKED:", error)
        return 1
    print(f"OpenAPI inventory PASS: {len(current)} contracts; conservative checks only")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
