"""Synthetic-only vehicle CSV preflight; never assigns owner, history or DB rows.

The full VIN is accepted only for fictional fixtures and never included in output.
Production vehicles require separate authorization, consent, dedupe and release gate.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import stat
from pathlib import Path
from uuid import UUID

MAX_BYTES = 5_000_000
MAX_ROWS = 50_000
COLUMNS = ("record_type", "source_id", "organization_id", "location_id",
           "vin", "make", "model", "source_system")
SAFE_ID = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.")
VIN_ALLOWED = frozenset("ABCDEFGHJKLMNPRSTUVWXYZ0123456789")


def _valid_id(value: str, minimum: int, maximum: int) -> bool:
    return minimum <= len(value) <= maximum and all(ch in SAFE_ID for ch in value)


def _valid_label(value: str, maximum: int) -> bool:
    # Fictional fixture labels only, with no formula-like content or control chars.
    return (1 <= len(value) <= maximum and value == value.strip()
            and value[0] not in "=+-@"
            and all(ch.isascii() and (ch.isalnum() or ch in " -_.") for ch in value))


def inspect_vehicle_csv(path: Path) -> dict:
    """Return only error codes, physical line numbers and accepted counts."""
    def fail(code: str, row: int = 0):
        return {"ok": False, "counts": {"VEHICLE": 0},
                "errors": [{"row": row, "code": code}],
                "mode": "SYNTHETIC_VEHICLE_DRY_RUN", "writes": 0}

    try:
        if not hasattr(os, "O_NOFOLLOW"):
            return fail("UNAVAILABLE")
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW
                     | getattr(os, "O_BINARY", 0))
        with os.fdopen(fd, "rb") as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                return fail("UNAVAILABLE")
            data = handle.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            return fail("UNAVAILABLE")
        reader = csv.DictReader(io.StringIO(data.decode("utf-8-sig"), newline=""),
                                strict=True)
        if reader.fieldnames is None or tuple(reader.fieldnames) != COLUMNS:
            return fail("SCHEMA", 1)
        errors: list[dict] = []
        counts = {"VEHICLE": 0}
        seen_sources = set()
        seen_vins = set()
        physical = 0
        for row_no, row in enumerate(reader, start=2):
            physical += 1
            if physical > MAX_ROWS:
                errors.append({"row": row_no, "code": "ROW_LIMIT"})
                break
            if None in row or any(value is None for value in row.values()):
                errors.append({"row": row_no, "code": "SCHEMA"})
                continue
            if row["record_type"] != "VEHICLE":
                errors.append({"row": row_no, "code": "TYPE"})
                continue
            try:
                organization = str(UUID(row["organization_id"]))
                location = str(UUID(row["location_id"]))
            except ValueError:
                errors.append({"row": row_no, "code": "SCOPE"})
                continue
            if not _valid_id(row["source_id"], 1, 64):
                errors.append({"row": row_no, "code": "SOURCE_ID"})
                continue
            if not _valid_id(row["source_system"], 3, 32):
                errors.append({"row": row_no, "code": "PROVENANCE"})
                continue
            vin = row["vin"]
            if len(vin) != 17 or any(char not in VIN_ALLOWED for char in vin):
                errors.append({"row": row_no, "code": "VIN_FORMAT"})
                continue
            if not _valid_label(row["make"], 100) or not _valid_label(row["model"], 120):
                errors.append({"row": row_no, "code": "LABEL"})
                continue
            identity = (organization, location, row["source_system"], row["source_id"])
            if identity in seen_sources:
                errors.append({"row": row_no, "code": "DUPLICATE_SOURCE"})
                continue
            # VIN uniqueness is global, matching auto_vehicle.vin UNIQUE.
            if vin in seen_vins:
                errors.append({"row": row_no, "code": "DUPLICATE_VIN"})
                continue
            seen_sources.add(identity)
            seen_vins.add(vin)
            counts["VEHICLE"] += 1
        if physical == 0:
            errors.append({"row": 0, "code": "EMPTY_INPUT"})
        return {"ok": not errors, "counts": counts, "errors": errors,
                "mode": "SYNTHETIC_VEHICLE_DRY_RUN", "writes": 0}
    except (OSError, UnicodeError, csv.Error):
        return fail("UNREADABLE")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--synthetic-only", action="store_true", required=True)
    parser.add_argument("csv", type=Path)
    args = parser.parse_args()
    report = inspect_vehicle_csv(args.csv)
    print(json.dumps(report, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
