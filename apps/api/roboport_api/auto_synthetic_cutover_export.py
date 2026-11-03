"""Safe offline export of a fully reconciled synthetic three-domain cutover.

No public HTTP route; never exports customer ownership, history or documents.
The caller must hold live MANAGER and WAREHOUSE grants for the exact branch.
"""
from __future__ import annotations

from hashlib import sha256
import csv
import io
import json
import os
from pathlib import Path
import shutil
import stat
from uuid import UUID

import psycopg

from .auto_synthetic_cutover import (
    SyntheticCutoverDenied, SyntheticCutoverSources,
    reconcile_synthetic_cutover,
)
from scripts.auto_vehicle_migration_preflight import (
    MAX_BYTES as VEHICLE_LIMIT, MAX_ROWS as VEHICLE_ROWS,
    COLUMNS as VEHICLE_COLUMNS, VIN_ALLOWED, _valid_id, _valid_label,
)
from roboport_api.auto_service_import import (
    MAX_BYTES as SERVICE_LIMIT, MAX_ROWS as SERVICE_ROWS,
    FIELDS as SERVICE_COLUMNS, IDENT as SERVICE_IDENT, TITLE as SERVICE_TITLE,
)
from scripts.auto_migration_dry_run import (
    MAX_FILE_BYTES as STOCK_LIMIT, MAX_ROWS as STOCK_ROWS,
    COLUMNS as STOCK_COLUMNS, SAFE_ARTICLE,
)


_FILES = (
    ("vehicles.csv", "vehicle_path", "vehicle_sha256", "vehicle_batch_id", VEHICLE_LIMIT),
    ("services.csv", "service_path", "service_sha256", "service_batch_id", SERVICE_LIMIT),
    ("inventory.csv", "inventory_path", "inventory_sha256", "inventory_batch_id", STOCK_LIMIT),
)


def _snapshot(path: Path, digest: str, limit: int) -> bytes:
    """Read one regular-file descriptor; reject symlinks, FIFO and swapped bytes."""
    try:
        if not hasattr(os, "O_NOFOLLOW"):
            raise SyntheticCutoverDenied("SOURCE_UNAVAILABLE")
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW
                     | getattr(os, "O_BINARY", 0))
        with os.fdopen(fd, "rb") as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                raise SyntheticCutoverDenied("SOURCE_UNAVAILABLE")
            raw = handle.read(limit + 1)
        if len(raw) > limit or sha256(raw).hexdigest() != digest:
            raise SyntheticCutoverDenied("SOURCE_MISMATCH")
        return raw
    except OSError:
        raise SyntheticCutoverDenied("SOURCE_UNAVAILABLE") from None


def _write_private(directory: Path, name: str, raw: bytes) -> None:
    # Exclusive creation: no following a pre-planted file or overwriting a report.
    fd = os.open(directory / name,
                 os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())


def export_reconciled_synthetic_cutover(
    conn: psycopg.Connection, token: str, *, organization_id: UUID,
    location_id: UUID, sources: SyntheticCutoverSources,
    destination: Path, synthetic_only: bool = False,
) -> dict:
    """Export 3 source-byte identical CSVs plus an auditable synthetic manifest.

    Database access remains read-only. Refuse existing destination, foreign
    branch, revoked grants, tampered source, mismatched activation or owner link.
    A failed export removes only a newly created exclusive output directory.
    """
    if (not isinstance(destination, Path) or not destination.name
            or destination.name in (".", "..")
            or destination.is_absolute() and str(destination) == "/"):
        raise SyntheticCutoverDenied("INVALID_OUTPUT")
    parent = destination.parent
    if not parent.is_dir() or parent.is_symlink():
        raise SyntheticCutoverDenied("INVALID_OUTPUT")

    # Keep authorization row locks and a consistent reconciliation view while
    # re-reading the sources. Never expose unverified source content.
    with conn.transaction():
        report = reconcile_synthetic_cutover(
            conn, token, organization_id=organization_id,
            location_id=location_id, sources=sources, synthetic_only=synthetic_only,
        )
        originals = []
        for filename, source_attr, hash_attr, batch_attr, limit in _FILES:
            raw = _snapshot(
                getattr(sources, source_attr), getattr(sources, hash_attr), limit,
            )
            originals.append((filename, raw, getattr(sources, hash_attr),
                              str(getattr(sources, batch_attr))))
        manifest = {
            "version": 1,
            "kind": "SYNTHETIC_CUTOVER_EXPORT",
            "productionApproved": False,
            "organizationId": str(organization_id),
            "locationId": str(location_id),
            "counts": {
                "vehicles": report["vehicles"], "services": report["services"],
                "stockRows": report["stockRows"], "skus": report["skus"],
                "receipts": report["receipts"],
            },
            "files": {
                filename: {"sha256": digest, "bytes": len(raw), "batchId": batch}
                for filename, raw, digest, batch in originals
            },
        }
        raw_manifest = (
            json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode("utf-8")
        try:
            # mkdir rejects both existing files and symlink destinations.
            os.mkdir(destination, 0o700)
        except OSError:
            raise SyntheticCutoverDenied("OUTPUT_UNAVAILABLE") from None
        try:
            for filename, raw, _, _ in originals:
                _write_private(destination, filename, raw)
            _write_private(destination, "manifest.json", raw_manifest)
        except OSError:
            shutil.rmtree(destination)
            raise SyntheticCutoverDenied("OUTPUT_UNAVAILABLE") from None
        return {
            "ok": True, "mode": "SYNTHETIC_CUTOVER_EXPORT",
            "files": len(originals) + 1, "writes": 0,
            "manifestSha256": sha256(raw_manifest).hexdigest(),
            "productionApproved": False,
        }



def _synthetic_bundle_counts(raws: dict[str, bytes], organization: str,
                             location: str) -> dict[str, int]:
    """Validate ALL source rows, not merely hashes supplied by a manifest.

    No customer fields, cross-branch inventory, duplicate VIN/IDs, invalid
    service durations or missing SKU links. Only counters escape this helper.
    """
    def decode(name: str, columns: tuple[str, ...], limit: int) -> list[dict]:
        try:
            reader = csv.DictReader(
                io.StringIO(raws[name].decode("utf-8-sig"), newline=""), strict=True,
            )
            if reader.fieldnames is None or tuple(reader.fieldnames) != columns:
                raise SyntheticCutoverDenied("INVALID_EXPORT")
            rows = list(reader)
            if not 0 < len(rows) <= limit or any(
                None in row or any(v is None for v in row.values()) for row in rows
            ):
                raise SyntheticCutoverDenied("INVALID_EXPORT")
            return rows
        except (csv.Error, UnicodeError, ValueError):
            raise SyntheticCutoverDenied("INVALID_EXPORT") from None

    vehicles = decode("vehicles.csv", VEHICLE_COLUMNS, VEHICLE_ROWS)
    services = decode("services.csv", SERVICE_COLUMNS, SERVICE_ROWS)
    stock = decode("inventory.csv", STOCK_COLUMNS, STOCK_ROWS)
    seen_vins: set[str] = set()
    seen_vehicle_sources: set[tuple[str, str]] = set()
    for row in vehicles:
        source = (row["source_system"], row["source_id"])
        vin = row["vin"]
        if (row["record_type"] != "VEHICLE"
                or row["organization_id"] != organization
                or row["location_id"] != location
                or not _valid_id(source[0], 3, 32)
                or not _valid_id(source[1], 1, 64)
                or len(vin) != 17 or any(c not in VIN_ALLOWED for c in vin)
                or not _valid_label(row["make"], 100)
                or not _valid_label(row["model"], 120)
                or source in seen_vehicle_sources or vin in seen_vins):
            raise SyntheticCutoverDenied("INVALID_EXPORT")
        seen_vehicle_sources.add(source)
        seen_vins.add(vin)

    seen_service_sources: set[tuple[str, str]] = set()
    seen_codes: set[str] = set()
    for row in services:
        source = (row["source_system"], row["source_id"])
        code = row["code"]
        title = row["title"]
        duration = row["duration_minutes"]
        if (row["record_type"] != "SERVICE"
                or not 3 <= len(source[0]) <= 32
                or not 1 <= len(source[1]) <= 64
                or not 3 <= len(code) <= 64
                or any(c not in SERVICE_IDENT for v in (*source, code) for c in v)
                or not 1 <= len(title) <= 120
                or title != title.strip() or title[0] in "=+-@"
                or any(c not in SERVICE_TITLE for c in title)
                or not duration.isascii() or not duration.isdecimal()
                or not 5 <= int(duration) <= 1440
                or str(int(duration)) != duration
                or source in seen_service_sources or code in seen_codes):
            raise SyntheticCutoverDenied("INVALID_EXPORT")
        seen_service_sources.add(source)
        seen_codes.add(code)

    sources: set[str] = set()
    skus: set[str] = set()
    stocks: set[str] = set()
    totals: dict[str, int] = {}
    receipts = 0
    for row in stock:
        kind = row["record_type"]
        source = row["source_id"]
        article = row["article"]
        quantity = row["quantity"]
        if (kind not in ("SKU", "STOCK")
                or row["organization_id"] != organization
                or row["location_id"] != location
                or not 1 <= len(source) <= 64
                or not 3 <= len(article) <= 64
                or any(c not in SAFE_ARTICLE for v in (source, article) for c in v)
                or source in sources
                or not quantity.isascii() or not quantity.isdecimal()
                or str(int(quantity)) != quantity
                or not 0 <= int(quantity) <= 1_000_000_000
                or (kind == "SKU" and quantity != "0")):
            raise SyntheticCutoverDenied("INVALID_EXPORT")
        sources.add(source)
        if kind == "SKU":
            if article in skus:
                raise SyntheticCutoverDenied("INVALID_EXPORT")
            skus.add(article)
        else:
            stocks.add(article)
            totals[article] = totals.get(article, 0) + int(quantity)
            if totals[article] > 1_000_000_000:
                raise SyntheticCutoverDenied("INVALID_EXPORT")
            receipts += int(quantity) > 0
    if not skus or not stocks.issubset(skus):
        raise SyntheticCutoverDenied("INVALID_EXPORT")
    return {
        "vehicles": len(vehicles), "services": len(services),
        "stockRows": len(stock), "skus": len(skus), "receipts": receipts,
    }


def verify_synthetic_cutover_export(destination: Path) -> dict:
    """Verify copied bytes, bounds, inventory and privacy of an offline bundle.

    This is an integrity check, NOT proof of the legal basis or source
    authenticity. All errors are generic and never reveal source CSV values.
    """
    try:
        if (not isinstance(destination, Path) or destination.is_symlink()
                or not destination.is_dir() or not hasattr(os, "O_NOFOLLOW")
                or stat.S_IMODE(destination.stat().st_mode) & 0o077):
            raise SyntheticCutoverDenied("INVALID_EXPORT")
        if {p.name for p in destination.iterdir()} != {
            "vehicles.csv", "services.csv", "inventory.csv", "manifest.json",
        }:
            raise SyntheticCutoverDenied("INVALID_EXPORT")

        def read(name: str, maximum: int) -> bytes:
            p = destination / name
            fd = os.open(p, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
            with os.fdopen(fd, "rb") as stream:
                meta = os.fstat(stream.fileno())
                if (not stat.S_ISREG(meta.st_mode)
                        or stat.S_IMODE(meta.st_mode) & 0o077):
                    raise SyntheticCutoverDenied("INVALID_EXPORT")
                data = stream.read(maximum + 1)
            if len(data) > maximum:
                raise SyntheticCutoverDenied("INVALID_EXPORT")
            return data

        raw_manifest = read("manifest.json", 8192)
        doc = json.loads(raw_manifest)
        if (type(doc) is not dict or set(doc) != {
            "version","kind","productionApproved","organizationId",
            "locationId","counts","files",
        } or doc["version"] != 1
                or doc["kind"] != "SYNTHETIC_CUTOVER_EXPORT"
                or doc["productionApproved"] is not False
                or type(doc["counts"]) is not dict
                or set(doc["counts"]) != {
                    "vehicles","services","stockRows","skus","receipts",
                } or any(type(n) is not int or n < 0 for n in doc["counts"].values())
                or type(doc["files"]) is not dict
                or set(doc["files"]) != {entry[0] for entry in _FILES}):
            raise SyntheticCutoverDenied("INVALID_EXPORT")
        UUID(doc["organizationId"])
        UUID(doc["locationId"])
        raw_files: dict[str, bytes] = {}
        batch_ids: set[UUID] = set()
        organization = str(UUID(doc["organizationId"]))
        location = str(UUID(doc["locationId"]))
        if organization != doc["organizationId"] or location != doc["locationId"]:
            raise SyntheticCutoverDenied("INVALID_EXPORT")
        for name, _, _, _, limit in _FILES:
            entry = doc["files"][name]
            if (type(entry) is not dict or set(entry) != {
                "sha256","bytes","batchId",
            } or type(entry["sha256"]) is not str
                    or len(entry["sha256"]) != 64
                    or any(c not in "0123456789abcdef" for c in entry["sha256"])
                    or type(entry["bytes"]) is not int
                    or not 0 < entry["bytes"] <= limit):
                raise SyntheticCutoverDenied("INVALID_EXPORT")
            batch = UUID(entry["batchId"])
            if str(batch) != entry["batchId"] or batch in batch_ids:
                raise SyntheticCutoverDenied("INVALID_EXPORT")
            batch_ids.add(batch)
            raw = read(name, limit)
            if len(raw) != entry["bytes"] or sha256(raw).hexdigest() != entry["sha256"]:
                raise SyntheticCutoverDenied("INVALID_EXPORT")
            raw_files[name] = raw
        if _synthetic_bundle_counts(raw_files, organization, location) != doc["counts"]:
            raise SyntheticCutoverDenied("INVALID_EXPORT")
        return {
            "ok": True, "mode": "SYNTHETIC_CUTOVER_EXPORT_VERIFIED",
            "files": 4, "counts": doc["counts"], "productionApproved": False,
            "writes": 0,
            "manifestSha256": sha256(raw_manifest).hexdigest(),
        }
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        raise SyntheticCutoverDenied("INVALID_EXPORT") from None
