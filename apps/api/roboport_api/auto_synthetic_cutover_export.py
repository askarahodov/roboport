"""Safe offline export of a fully reconciled synthetic three-domain cutover.

No public HTTP route; never exports customer ownership, history or documents.
The caller must hold live MANAGER and WAREHOUSE grants for the exact branch.
"""
from __future__ import annotations

from hashlib import sha256
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
from scripts.auto_vehicle_migration_preflight import MAX_BYTES as VEHICLE_LIMIT
from roboport_api.auto_service_import import MAX_BYTES as SERVICE_LIMIT
from scripts.auto_migration_dry_run import MAX_FILE_BYTES as STOCK_LIMIT


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
            UUID(entry["batchId"])
            raw = read(name, limit)
            if len(raw) != entry["bytes"] or sha256(raw).hexdigest() != entry["sha256"]:
                raise SyntheticCutoverDenied("INVALID_EXPORT")
        return {
            "ok": True, "mode": "SYNTHETIC_CUTOVER_EXPORT_VERIFIED",
            "files": 4, "counts": doc["counts"], "productionApproved": False,
            "writes": 0,
            "manifestSha256": sha256(raw_manifest).hexdigest(),
        }
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        raise SyntheticCutoverDenied("INVALID_EXPORT") from None
