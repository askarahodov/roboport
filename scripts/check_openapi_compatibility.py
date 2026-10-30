"""Conservative OpenAPI compatibility gate for #119.

Checks removals of paths, HTTP methods, documented response codes, top-level
component schemas/properties. This is not a complete semantic API diff.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import yaml

HTTP_METHODS = frozenset({"get", "post", "put", "patch", "delete", "head", "options", "trace"})


def regressions(before: object, after: object) -> list[str]:
    if not isinstance(before, dict) or not isinstance(after, dict):
        return ["INVALID_OPENAPI"]
    errors = []
    old_paths, new_paths = before.get("paths"), after.get("paths")
    if not isinstance(old_paths, dict) or not isinstance(new_paths, dict):
        return ["INVALID_OPENAPI_PATHS"]
    for path, old in old_paths.items():
        if path not in new_paths:
            errors.append(f"REMOVED_PATH:{path}")
            continue
        new = new_paths[path]
        if not isinstance(old, dict) or not isinstance(new, dict):
            errors.append(f"INVALID_PATH:{path}")
            continue
        for verb in HTTP_METHODS.intersection(old):
            if verb not in new:
                errors.append(f"REMOVED_OPERATION:{verb.upper()}:{path}")
                continue
            op_old, op_new = old[verb], new[verb]
            if not isinstance(op_old, dict) or not isinstance(op_new, dict):
                errors.append(f"INVALID_OPERATION:{verb.upper()}:{path}")
                continue
            old_params = {(p.get("in"), p.get("name")): p for p in op_old.get("parameters", []) if isinstance(p, dict)}
            new_params = {(p.get("in"), p.get("name")): p for p in op_new.get("parameters", []) if isinstance(p, dict)}
            for key, param in new_params.items():
                if param.get("required") is True and (key not in old_params or old_params[key].get("required") is not True):
                    errors.append(f"NEW_REQUIRED_PARAMETER:{verb.upper()}:{path}:{key}")
            for key in old_params.keys() - new_params.keys():
                errors.append(f"REMOVED_PARAMETER:{verb.upper()}:{path}:{key}")
            responses_old, responses_new = op_old.get("responses", {}), op_new.get("responses", {})
            if not isinstance(responses_old, dict) or not isinstance(responses_new, dict):
                errors.append(f"INVALID_RESPONSES:{verb.upper()}:{path}")
                continue
            for status in set(responses_old) - set(responses_new):
                errors.append(f"REMOVED_RESPONSE:{verb.upper()}:{path}:{status}")
    old_schemas = before.get("components", {}).get("schemas", {})
    new_schemas = after.get("components", {}).get("schemas", {})
    if not isinstance(old_schemas, dict) or not isinstance(new_schemas, dict):
        return errors + ["INVALID_SCHEMAS"]
    for name, old in old_schemas.items():
        if name not in new_schemas:
            errors.append(f"REMOVED_SCHEMA:{name}")
            continue
        current = new_schemas[name]
        if not isinstance(old, dict) or not isinstance(current, dict):
            errors.append(f"INVALID_SCHEMA:{name}")
            continue
        old_required, new_required = old.get("required", []), current.get("required", [])
        if isinstance(old_required, list) and isinstance(new_required, list):
            for prop in set(new_required) - set(old_required):
                errors.append(f"NEW_REQUIRED_PROPERTY:{name}.{prop}")
        else:
            errors.append(f"INVALID_REQUIRED:{name}")
        if old.get("type") != current.get("type"):
            errors.append(f"CHANGED_TYPE:{name}")
        old_props, new_props = old.get("properties", {}), current.get("properties", {})
        if isinstance(old_props, dict) and isinstance(new_props, dict):
            for prop in set(old_props) - set(new_props):
                errors.append(f"REMOVED_PROPERTY:{name}.{prop}")
            for prop in set(old_props) & set(new_props):
                earlier, later = old_props[prop], new_props[prop]
                if isinstance(earlier, dict) and isinstance(later, dict) and earlier.get("type") != later.get("type"):
                    errors.append(f"CHANGED_PROPERTY_TYPE:{name}.{prop}")
        else:
            errors.append(f"INVALID_PROPERTIES:{name}")
    return sorted(errors)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before", required=True, type=Path)
    parser.add_argument("--after", required=True, type=Path)
    args = parser.parse_args()
    try:
        old = yaml.safe_load(args.before.read_text(encoding="utf-8"))
        new = yaml.safe_load(args.after.read_text(encoding="utf-8"))
        problems = regressions(old, new)
    except (OSError, yaml.YAMLError):
        problems = ["OPENAPI_UNAVAILABLE"]
    if problems:
        for item in problems:
            print("BLOCKED:", item)
        return 1
    print("OpenAPI removal compatibility PASS (not a complete semantic diff)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
