"""Fail-closed migration provenance inventory for customers, counterparties, history, documents.

Never opens or emits payloads. Does NOT grant consent or authorize production import.
Only a preflight of synthetic source descriptors; the actual customer/history migration
must remain blocked pending independent legal/security/ops decisions under #119.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
from pathlib import Path
from uuid import UUID

KINDS = ("CUSTOMER", "COUNTERPARTY", "REPAIR_HISTORY", "DOCUMENT")
SHA = re.compile(r"^[0-9a-f]{64}$")
NAME = re.compile(r"^[A-Z0-9][A-Z0-9_.-]{0,63}$")
MAX_MANIFEST = 100_000
MAX_FILES = 64
MAX_BYTES = 10_000_000
MAX_BUNDLE_BYTES = 40_000_000
ALLOWED = frozenset({
    "schema", "synthetic_only", "organization_id", "location_id", "sources"
})
SOURCE_FIELDS = frozenset({"kind", "file", "sha256", "bytes", "records",
                           "source_system", "legal_basis_ref", "owner_proof_ref"})
EMPTY_REPORT = {"writes": 0, "mode": "PROVENANCE_PREFLIGHT_ONLY"}


class ProvenanceDenied(ValueError):
    """Stable errors only: no paths, filenames, customer IDs or personal data."""


def _read_regular(path: Path, cap: int) -> bytes:
    if not hasattr(os, "O_NOFOLLOW"):
        raise ProvenanceDenied("UNAVAILABLE")
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ProvenanceDenied("UNAVAILABLE")
            data = stream.read(cap + 1)
    except OSError:
        raise ProvenanceDenied("UNAVAILABLE") from None
    if len(data) > cap:
        raise ProvenanceDenied("LIMIT")
    return data


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ProvenanceDenied("SCHEMA")
        result[key] = value
    return result


def _reject_nonfinite_number(_: str) -> None:
    raise ProvenanceDenied("SCHEMA")


def inspect_provenance_bundle(manifest: Path) -> dict:
    """Inspect metadata and SHA256 only; never return source identifiers."""
    try:
        raw = _read_regular(manifest, MAX_MANIFEST)
        payload = json.loads(raw, object_pairs_hook=_reject_duplicate_keys,
                             parse_constant=_reject_nonfinite_number)
        if not isinstance(payload, dict) or set(payload) != ALLOWED:
            raise ProvenanceDenied("SCHEMA")
        if type(payload["schema"]) is not int or payload["schema"] != 1 or payload["synthetic_only"] is not True:
            raise ProvenanceDenied("APPROVAL_REQUIRED")
        for key in ("organization_id", "location_id"):
            if type(payload[key]) is not str or str(UUID(payload[key])) != payload[key]:
                raise ProvenanceDenied("SCOPE")
        entries = payload["sources"]
        if not isinstance(entries, list) or not 1 <= len(entries) <= MAX_FILES:
            raise ProvenanceDenied("SCHEMA")
        counts = {kind: 0 for kind in KINDS}
        seen = set()
        total_bytes = 0
        for entry in entries:
            if not isinstance(entry, dict) or set(entry) != SOURCE_FIELDS:
                raise ProvenanceDenied("SCHEMA")
            kind = entry["kind"]
            if kind not in KINDS:
                raise ProvenanceDenied("TYPE")
            filename = entry["file"]
            if (type(filename) is not str or not NAME.fullmatch(filename)
                    or filename in seen):
                raise ProvenanceDenied("SOURCE_NAME")
            seen.add(filename)
            if (type(entry["sha256"]) is not str or not SHA.fullmatch(entry["sha256"])
                    or type(entry["bytes"]) is not int
                    or not 1 <= entry["bytes"] <= MAX_BYTES
                    or type(entry["records"]) is not int
                    or not 1 <= entry["records"] <= 50000):
                raise ProvenanceDenied("METADATA")
            total_bytes += entry["bytes"]
            if total_bytes > MAX_BUNDLE_BYTES:
                raise ProvenanceDenied("BUNDLE_LIMIT")
            if type(entry["source_system"]) is not str or not NAME.fullmatch(entry["source_system"]):
                raise ProvenanceDenied("PROVENANCE")
            # These are opaque identifiers for future independent evidence review.
            # Presence is not proof of consent or legal basis.
            for evidence in ("legal_basis_ref", "owner_proof_ref"):
                value = entry[evidence]
                if type(value) is not str or not NAME.fullmatch(value):
                    raise ProvenanceDenied("EVIDENCE_REQUIRED")
            file_bytes = _read_regular(manifest.parent / filename, MAX_BYTES)
            if (len(file_bytes) != entry["bytes"]
                    or hashlib.sha256(file_bytes).hexdigest() != entry["sha256"]):
                raise ProvenanceDenied("SOURCE_MISMATCH")
            counts[kind] += entry["records"]
        if any(counts[kind] == 0 for kind in KINDS):
            raise ProvenanceDenied("SCOPE_INCOMPLETE")
        # The presence of opaque evidence references cannot assert legal approval.
        return {**EMPTY_REPORT, "ok": True, "counts": counts,
                "approval": "NOT_VERIFIED", "release_allowed": False}
    except (ValueError, TypeError, UnicodeError, json.JSONDecodeError) as error:
        if isinstance(error, ProvenanceDenied):
            return {**EMPTY_REPORT, "ok": False, "code": str(error),
                    "release_allowed": False}
        return {**EMPTY_REPORT, "ok": False, "code": "SCHEMA",
                "release_allowed": False}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--synthetic-only", action="store_true", required=True)
    parser.add_argument("manifest", type=Path)
    args = parser.parse_args()
    result = inspect_provenance_bundle(args.manifest)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
