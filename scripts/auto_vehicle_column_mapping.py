"""Strict synthetic vehicle CSV mapping into the existing canonical import schema.

Never reads owner/history columns or prints identifiers. No database writes.
Mapping is explicit and complete; no fuzzy header or value inference.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import stat
import tempfile
from pathlib import Path

from scripts.auto_vehicle_migration_preflight import COLUMNS, MAX_BYTES, MAX_ROWS, inspect_vehicle_csv


class VehicleMappingRejected(ValueError):
    """Error codes only; never include row values, paths or sensitive headers."""


def map_synthetic_vehicle_csv(source: Path, target: Path, *, mapping: dict[str, str]) -> dict:
    """Canonicalize an explicitly declared synthetic CSV using atomic fail-closed output."""
    if (type(mapping) is not dict or len(mapping) != len(COLUMNS)
            or set(mapping.values()) != set(COLUMNS)
            or any(type(k) is not str or not k or type(v) is not str
                   for k, v in mapping.items())):
        raise VehicleMappingRejected("MAPPING")
    if not hasattr(os, "O_NOFOLLOW"):
        raise VehicleMappingRejected("UNAVAILABLE")
    try:
        fd = os.open(source, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW
                     | getattr(os, "O_BINARY", 0))
        with os.fdopen(fd, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise VehicleMappingRejected("UNAVAILABLE")
            raw = stream.read(MAX_BYTES + 1)
    except OSError:
        raise VehicleMappingRejected("UNAVAILABLE") from None
    if len(raw) > MAX_BYTES:
        raise VehicleMappingRejected("FILE_LIMIT")
    try:
        reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig"), newline=""),
                                strict=True)
        headers = reader.fieldnames
        if (headers is None or len(headers) != len(mapping)
                or len(set(headers)) != len(headers)
                or set(headers) != set(mapping)):
            raise VehicleMappingRejected("SCHEMA")
        output = io.StringIO(newline="")
        writer = csv.DictWriter(output, fieldnames=COLUMNS, lineterminator="\n")
        writer.writeheader()
        count = 0
        for row in reader:
            count += 1
            if count > MAX_ROWS:
                raise VehicleMappingRejected("ROW_LIMIT")
            if None in row or any(value is None for value in row.values()):
                raise VehicleMappingRejected("SCHEMA")
            writer.writerow({mapping[key]: value for key, value in row.items()})
        canonical = output.getvalue().encode("utf-8")
    except (UnicodeError, csv.Error):
        raise VehicleMappingRejected("UNREADABLE") from None
    if len(canonical) > MAX_BYTES:
        raise VehicleMappingRejected("FILE_LIMIT")
    # Reuse the *existing* VIN, tenant/source dedupe and formula guards.
    with tempfile.TemporaryDirectory(prefix="vehicle-map-") as tmp:
        candidate = Path(tmp) / "canonical.csv"
        candidate.write_bytes(canonical)
        report = inspect_vehicle_csv(candidate)
    if not report["ok"]:
        raise VehicleMappingRejected("PREFLIGHT")
    # Publish only a complete private file; hard links fail if target already exists.
    # A temporary file is placed in the target directory for same-filesystem linking.
    temporary = None
    try:
        fd, name = tempfile.mkstemp(prefix=".vehicle-map-", suffix=".tmp",
                                    dir=target.parent)
        temporary = Path(name)
        with os.fdopen(fd, "wb") as stream:
            stream.write(canonical)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, target, follow_symlinks=False)
    except (OSError, ValueError):
        raise VehicleMappingRejected("OUTPUT_UNAVAILABLE") from None
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
    return {"ok": True, "count": count, "sha256": hashlib.sha256(canonical).hexdigest(),
            "writes": 0, "mode": "SYNTHETIC_VEHICLE_MAPPING"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--synthetic-only", action="store_true", required=True)
    parser.add_argument("--field", action="append", required=True, metavar="SOURCE=TARGET",
                        help="Repeat for each of the eight canonical fields.")
    parser.add_argument("source", type=Path)
    parser.add_argument("target", type=Path)
    args = parser.parse_args()
    mapping = {}
    for raw in args.field:
        if "=" not in raw:
            parser.error("each --field requires SOURCE=TARGET")
        key, value = raw.split("=", 1)
        if key in mapping:
            parser.error("duplicate source field")
        mapping[key] = value
    try:
        result = map_synthetic_vehicle_csv(args.source, args.target, mapping=mapping)
    except VehicleMappingRejected as error:
        print(json.dumps({"ok": False, "code": str(error), "writes": 0},
                         sort_keys=True))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
