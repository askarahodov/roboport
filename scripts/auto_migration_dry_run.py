"""Fail-closed offline automotive CSV migration preflight. Never writes a DB.

Synthetic-only: raw customer names, full VIN, phones and repair history are
explicitly not accepted. Output contains counts and opaque row numbers only.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path

MAX_FILE_BYTES = 5_000_000
MAX_ROWS = 50_000
COLUMNS = ("record_type", "source_id", "organization_id", "location_id",
           "article", "quantity")
TYPES = frozenset({"SKU", "STOCK"})
SAFE_ARTICLE = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.")


def inspect_csv(path: Path) -> dict:
    """Deterministic report without echoing any source data."""
    errors: list[dict] = []
    stats = {"SKU": 0, "STOCK": 0}
    seen: set[tuple[str, str, str]] = set()
    sku_keys: set[tuple[str, str, str]] = set()
    stock_keys: set[tuple[str, str, str]] = set()
    physical = 0
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
        return {"ok": False, "counts": stats, "errors": [{"row": 0, "code": "UNAVAILABLE"}]}
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, strict=True)
        if reader.fieldnames is None or tuple(reader.fieldnames) != COLUMNS:
            return {"ok": False, "counts": stats, "errors": [{"row": 1, "code": "SCHEMA"}]}
        for row_no, row in enumerate(reader, start=2):
            physical += 1
            if physical > MAX_ROWS:
                errors.append({"row": row_no, "code": "ROW_LIMIT"})
                break
            if None in row or any(value is None for value in row.values()):
                errors.append({"row": row_no, "code": "SCHEMA"})
                continue
            kind = row["record_type"]
            if kind not in TYPES:
                errors.append({"row": row_no, "code": "TYPE"})
                continue
            source = row["source_id"]
            org, loc = row["organization_id"], row["location_id"]
            article = row["article"]
            quantity = row["quantity"]
            import uuid
            try:
                org_id, loc_id = str(uuid.UUID(org)), str(uuid.UUID(loc))
            except ValueError:
                errors.append({"row": row_no, "code": "SCOPE"})
                continue
            if not 1 <= len(source) <= 64 or any(c not in SAFE_ARTICLE for c in source):
                errors.append({"row": row_no, "code": "SOURCE_ID"})
                continue
            if not 3 <= len(article) <= 64 or any(c not in SAFE_ARTICLE for c in article):
                errors.append({"row": row_no, "code": "ARTICLE"})
                continue
            try:
                qty = int(quantity)
                if str(qty) != quantity or qty < 0 or qty > 1_000_000_000:
                    raise ValueError
                if kind == "SKU" and qty != 0:
                    raise ValueError
            except ValueError:
                errors.append({"row": row_no, "code": "QUANTITY"})
                continue
            identity = (org_id, loc_id, source)
            if identity in seen:
                errors.append({"row": row_no, "code": "DUPLICATE_SOURCE"})
                continue
            seen.add(identity)
            sku_key = (org_id, loc_id, article)
            if kind == "SKU":
                if sku_key in sku_keys:
                    errors.append({"row": row_no, "code": "DUPLICATE_SKU"})
                    continue
                sku_keys.add(sku_key)
            else:
                stock_keys.add(sku_key)
            stats[kind] += 1
    for key in sorted(stock_keys - sku_keys):
        # Do not emit the SKU/tenant identifier, even from synthetic fixtures.
        errors.append({"row": 0, "code": "STOCK_WITHOUT_SKU"})
    if physical == 0:
        errors.append({"row": 0, "code": "EMPTY_INPUT"})
    return {"ok": not errors, "counts": stats, "errors": errors,
            "mode": "DRY_RUN_ONLY", "writes": 0}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("csv", type=Path)
    p.add_argument("--synthetic-only", action="store_true", required=True)
    options = p.parse_args()
    try:
        report = inspect_csv(options.csv)
    except (OSError, UnicodeError, csv.Error):
        report = {"ok": False, "counts": {"SKU": 0, "STOCK": 0},
                  "errors": [{"row": 0, "code": "UNREADABLE"}], "mode": "DRY_RUN_ONLY", "writes": 0}
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
