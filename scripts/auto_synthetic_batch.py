"""All-or-nothing offline synthetic VEHICLE+SERVICE XLSX batch preparation.

No database writes, real customer data, owner/history or production approvals.
Each input must be independently pinned by a trusted SHA-256 before any output.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
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
    args = parser.parse_args()
    report = inspect_synthetic_batch(
        {"VEHICLE": args.vehicle, "SERVICE": args.service}, args.destination,
        expected_sha256={"VEHICLE": args.vehicle_sha256, "SERVICE": args.service_sha256},
    )
    print(json.dumps(report, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
