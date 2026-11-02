"""All-or-nothing offline synthetic VEHICLE+SERVICE XLSX batch preparation.

No database writes, real customer data, owner/history or production approvals.
Each input must be independently pinned by a trusted SHA-256 before any output.
"""
from __future__ import annotations

import argparse
import ctypes
import errno
import hashlib
import json
import os
import stat
import sys
import tempfile
from pathlib import Path

from scripts.auto_synthetic_xlsx import (
    SyntheticWorkbookDenied, convert_synthetic_xlsx,
)

KINDS = ("VEHICLE", "SERVICE")
MODE = "SYNTHETIC_XLSX_BATCH_ONLY"


class BatchDenied(ValueError):
    """Stable, redacted errors only."""


def prepare_synthetic_batch(
    sources: dict[str, Path], destination: Path, *,
    expected_sha256: dict[str, str],
    mappings: dict[str, dict[str, str]] | None = None,
) -> dict:
    """Validate both inputs, then publish both outputs; rollback on runtime failure.

    Publication is atomic per file. This is NOT a crash-atomic two-file commit;
    interrupted processes require inspection and cleanup before retry.
    """
    if (type(sources) is not dict or set(sources) != set(KINDS)
            or any(not isinstance(path, Path) for path in sources.values())
            or type(expected_sha256) is not dict
            or set(expected_sha256) != set(KINDS)
            or (mappings is not None and
                (type(mappings) is not dict or not set(mappings) <= set(KINDS)))):
        raise BatchDenied("SCHEMA")
    if (destination.is_symlink() or not destination.is_dir()
            or not stat.S_ISDIR(os.stat(destination, follow_symlinks=False).st_mode)):
        raise BatchDenied("OUTPUT_UNAVAILABLE")
    names = {kind: kind.lower() + ".csv" for kind in KINDS}
    targets = {kind: destination / names[kind] for kind in KINDS}
    if any(os.path.lexists(target) for target in targets.values()):
        raise BatchDenied("OUTPUT_CONFLICT")

    published: list[Path] = []
    try:
        with tempfile.TemporaryDirectory(prefix=".auto-batch-", dir=destination) as folder:
            prepared = {}
            for kind in KINDS:
                candidate = Path(folder) / names[kind]
                try:
                    report = convert_synthetic_xlsx(
                        sources[kind], candidate, kind=kind,
                        expected_sha256=expected_sha256[kind],
                        mapping=None if mappings is None else mappings.get(kind),
                    )
                except (SyntheticWorkbookDenied, OSError, TypeError, ValueError) as error:
                    code = str(error) if isinstance(error, SyntheticWorkbookDenied) else "UNREADABLE"
                    raise BatchDenied(code) from None
                prepared[kind] = report

            # No publish occurs until every source is verified and preflighted.
            for kind in KINDS:
                candidate = Path(folder) / names[kind]
                try:
                    os.link(candidate, targets[kind], follow_symlinks=False)
                except OSError:
                    raise BatchDenied("OUTPUT_UNAVAILABLE") from None
                published.append(targets[kind])
        return {
            "ok": True, "mode": MODE, "writes": 0,
            "rows": {kind: prepared[kind]["rows"] for kind in KINDS},
            "sha256": {kind: prepared[kind]["sha256"] for kind in KINDS},
            "release_allowed": False,
        }
    except Exception:
        for target in reversed(published):
            try:
                target.unlink()
            except OSError:
                pass
        raise


def _sync_directory(path: Path) -> None:
    """Durably persist directory entries, or refuse to promise durability."""
    if not all(hasattr(os, name) for name in ("O_DIRECTORY", "O_NOFOLLOW")):
        raise BatchDenied("OUTPUT_UNAVAILABLE")
    try:
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        raise BatchDenied("OUTPUT_UNAVAILABLE") from None


def _rename_directory_no_replace(source: Path, target: Path) -> None:
    """Linux renameat2(RENAME_NOREPLACE): no check-then-rename race."""
    if not sys.platform.startswith("linux"):
        raise BatchDenied("OUTPUT_UNAVAILABLE")
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        operation = libc.renameat2
        operation.argtypes = (ctypes.c_int, ctypes.c_char_p,
                              ctypes.c_int, ctypes.c_char_p, ctypes.c_uint)
        operation.restype = ctypes.c_int
        result = operation(-100, os.fsencode(source), -100, os.fsencode(target), 1)
    except (AttributeError, OSError, TypeError):
        raise BatchDenied("OUTPUT_UNAVAILABLE") from None
    if result != 0:
        code = ctypes.get_errno()
        raise BatchDenied("OUTPUT_CONFLICT" if code == errno.EEXIST
                          else "OUTPUT_UNAVAILABLE")


def prepare_atomic_bundle(
    sources: dict[str, Path], destination: Path, *,
    expected_sha256: dict[str, str],
    mappings: dict[str, dict[str, str]] | None = None,
) -> dict:
    """Publish both outputs under one durable no-clobber directory entry.

    Requires Linux renameat2 + directory fsync; all other systems fail closed.
    """
    if destination.is_symlink() or not destination.is_dir():
        raise BatchDenied("OUTPUT_UNAVAILABLE")
    final = destination / "vehicle-service-bundle"
    if os.path.lexists(final):
        raise BatchDenied("OUTPUT_CONFLICT")
    with tempfile.TemporaryDirectory(prefix=".auto-bundle-", dir=destination) as root:
        staged = Path(root) / "vehicle-service-bundle"
        staged.mkdir(mode=0o700)
        report = prepare_synthetic_batch(
            sources, staged, expected_sha256=expected_sha256, mappings=mappings
        )
        _sync_directory(staged)
        # Linux no-replace is atomic, including if another writer races here.
        try:
            _rename_directory_no_replace(staged, final)
        except OSError:
            raise BatchDenied("OUTPUT_UNAVAILABLE") from None
        # After rename, fsync may fail: return a refusal, but preserve published
        # output for operator inspection instead of deleting potentially valid data.
        _sync_directory(destination)
    return {**report, "mode": "SYNTHETIC_XLSX_ATOMIC_BUNDLE"}


def inspect_atomic_bundle(sources: dict[str, Path], destination: Path, *,
                          expected_sha256: dict[str, str],
                          mappings: dict[str, dict[str, str]] | None = None) -> dict:
    try:
        return prepare_atomic_bundle(
            sources, destination, expected_sha256=expected_sha256, mappings=mappings
        )
    except (BatchDenied, OSError, TypeError, ValueError) as error:
        return {
            "ok": False, "mode": "SYNTHETIC_XLSX_ATOMIC_BUNDLE", "writes": 0,
            "code": str(error) if isinstance(error, BatchDenied) else "UNAVAILABLE",
            "release_allowed": False,
        }


def inspect_synthetic_batch(sources: dict[str, Path], destination: Path, *,
                            expected_sha256: dict[str, str],
                            mappings: dict[str, dict[str, str]] | None = None) -> dict:
    try:
        return prepare_synthetic_batch(
            sources, destination, expected_sha256=expected_sha256, mappings=mappings
        )
    except (BatchDenied, OSError, TypeError, ValueError) as error:
        return {
            "ok": False, "mode": MODE, "writes": 0,
            "code": str(error) if isinstance(error, BatchDenied) else "UNAVAILABLE",
            "release_allowed": False,
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--synthetic-only", action="store_true", required=True)
    parser.add_argument("--vehicle", type=Path, required=True)
    parser.add_argument("--vehicle-sha256", required=True)
    parser.add_argument("--service", type=Path, required=True)
    parser.add_argument("--service-sha256", required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--atomic-bundle", action="store_true",
                        help="Publish the two files as a single directory entry.")
    parser.add_argument("--vehicle-field", action="append", metavar="SOURCE=TARGET",
                        help="Explicit full vehicle column mapping; repeat for each field.")
    parser.add_argument("--service-field", action="append", metavar="SOURCE=TARGET",
                        help="Explicit full service column mapping; repeat for each field.")
    args = parser.parse_args()
    mappings = {}
    for kind, fields in (("VEHICLE", args.vehicle_field), ("SERVICE", args.service_field)):
        if fields is None:
            continue
        entries = {}
        for field in fields:
            if "=" not in field:
                parser.error("each --vehicle-field/--service-field requires SOURCE=TARGET")
            source, target = field.split("=", 1)
            if not source or not target or source in entries:
                parser.error("invalid or duplicate source field")
            entries[source] = target
        mappings[kind] = entries
    runner = inspect_atomic_bundle if args.atomic_bundle else inspect_synthetic_batch
    report = runner(
        {"VEHICLE": args.vehicle, "SERVICE": args.service}, args.destination,
        expected_sha256={"VEHICLE": args.vehicle_sha256, "SERVICE": args.service_sha256},
        mappings=mappings or None,
    )
    print(json.dumps(report, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
