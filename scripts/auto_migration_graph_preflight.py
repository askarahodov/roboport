"""Синтетическая сверка семи источников перед импортом (#172).

Не переносит клиентов, историю или документы. Ссылки на доказательства —
только искусственные идентификаторы, никогда не юридическое разрешение.
"""
from __future__ import annotations

import argparse
import base64
import csv
from hashlib import sha256
import io
import json
from pathlib import Path
import re
from uuid import UUID

from scripts.auto_migration_provenance_bundle import (
    _read_regular, _reject_duplicate_keys, _reject_nonfinite_number, ProvenanceDenied,
)
from roboport_api.auto_cutover_prepare import (
    HEADERS, MAX_FILE, normalize_entry, PrepareDenied,
)
from roboport_api.auto_cutover_http import prepare_bundle, CutoverInput

# Оpaque synthetic references, never names, contacts, document contents or raw history.
BASE = ("record_type", "source_system", "source_id", "organization_id", "location_id",
        "legal_basis_ref", "owner_proof_ref")
RELATION_HEADERS = {
    "customers.csv": BASE,
    "counterparties.csv": BASE,
    "history.csv": BASE + ("customer_system", "customer_id", "vehicle_system", "vehicle_id",
                           "service_system", "service_id", "counterparty_system", "counterparty_id"),
    "documents.csv": BASE + ("history_system", "history_id", "document_sha256"),
}
TYPES = dict(zip(RELATION_HEADERS, ("CUSTOMER", "COUNTERPARTY", "REPAIR_HISTORY", "DOCUMENT")))
ALL_HEADERS = {**HEADERS, **RELATION_HEADERS}
SHA = re.compile(r"[0-9a-f]{64}")
REF = re.compile(r"SYN-[A-Z0-9][A-Z0-9_.-]{0,59}")
NAME = re.compile(r"[A-Z0-9][A-Z0-9_.-]{0,63}")
FIELDS = {"file", "format", "mapping", "sha256", "bytes", "records"}
MAX_MANIFEST = 100_000
BASE_REPORT = {"mode": "SYNTHETIC_GRAPH_PREFLIGHT", "writes": 0,
               "approval": "NOT_VERIFIED", "release_allowed": False}


class GraphDenied(ValueError):
    pass


def _require(condition, code):
    if not condition:
        raise GraphDenied(code)


def _scope(value):
    _require(type(value) is str and str(UUID(value)) == value, "SCOPE")
    return UUID(value)


def _rows(entry):
    raw = base64.b64decode(entry["base64"], validate=True)
    return list(csv.DictReader(io.StringIO(raw.decode("utf-8-sig"), newline=""), strict=True))


def _identity(row):
    return row["source_system"], row["source_id"]


def _reference(row, prefix):
    return row[prefix + "_system"], row[prefix + "_id"]


def inspect_graph(manifest: Path, *, expected_manifest_sha256: str) -> dict:
    """Read exact bytes once, validate entire graph, return only counts and stable codes."""
    try:
        _require(type(expected_manifest_sha256) is str and
                 SHA.fullmatch(expected_manifest_sha256), "MANIFEST_DIGEST_REQUIRED")
        raw_manifest = _read_regular(manifest, MAX_MANIFEST)
        _require(sha256(raw_manifest).hexdigest() == expected_manifest_sha256, "MANIFEST_MISMATCH")
        meta = json.loads(raw_manifest.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys,
                          parse_constant=_reject_nonfinite_number)
        _require(type(meta) is dict and set(meta) ==
                 {"schema", "synthetic_only", "organization_id", "location_id", "sources"}, "SCHEMA")
        _require(type(meta["schema"]) is int and meta["schema"] == 1 and
                 meta["synthetic_only"] is True, "APPROVAL_REQUIRED")
        org, loc = _scope(meta["organization_id"]), _scope(meta["location_id"])
        sources = meta["sources"]
        _require(type(sources) is dict and set(sources) == set(ALL_HEADERS), "SCOPE_INCOMPLETE")
        seen = set()
        # Validate all descriptors before reading source data; no paths or arbitrary payload files.
        for entry in sources.values():
            _require(type(entry) is dict and set(entry) == FIELDS, "SCHEMA")
            filename = entry["file"]
            _require(type(filename) is str and NAME.fullmatch(filename) and filename not in seen,
                     "SOURCE_NAME")
            seen.add(filename)
            _require(entry["format"] in ("CSV", "XLSX") and
                     type(entry["bytes"]) is int and 0 < entry["bytes"] <= MAX_FILE and
                     type(entry["records"]) is int and 0 < entry["records"] <= 2000 and
                     type(entry["sha256"]) is str and SHA.fullmatch(entry["sha256"]), "METADATA")
        canonical = {}
        rows = {}
        for name, headers in ALL_HEADERS.items():
            entry = sources[name]
            raw = _read_regular(manifest.parent / entry["file"], entry["bytes"])
            _require(len(raw) == entry["bytes"] and sha256(raw).hexdigest() == entry["sha256"],
                     "SOURCE_MISMATCH")
            canonical[name] = normalize_entry({
                "base64": base64.b64encode(raw).decode(), "sha256": entry["sha256"],
                "format": entry["format"], "mapping": entry["mapping"],
            }, headers)
            rows[name] = _rows(canonical[name])
            _require(len(rows[name]) == entry["records"], "COUNT_MISMATCH")
        # The three importable catalogs must satisfy the exact existing HTTP preview
        # semantics, including scope, dedupe, VIN, quantity and synthetic markers.
        _, _, counts, catalog_digest, _ = prepare_bundle(
            {"syntheticOnly": True, "files": {name: canonical[name] for name in HEADERS}},
            org, loc, "preview")
        index = {}
        for name in RELATION_HEADERS:
            records = {}
            for row in rows[name]:
                _require(row["record_type"] == TYPES[name] and
                         row["organization_id"] == str(org) and row["location_id"] == str(loc),
                         "SCOPE")
                _require(row["source_system"] == "DEMO" and all(
                    REF.fullmatch(row[field]) for field in
                    ("source_id", "legal_basis_ref", "owner_proof_ref")), "SYNTHETIC_REQUIRED")
                identity = _identity(row)
                _require(identity not in records, "DUPLICATE_SOURCE")
                records[identity] = row
            index[name] = records
        vehicles = {_identity(row) for row in rows["vehicles.csv"]}
        services = {_identity(row) for row in rows["services.csv"]}
        for row in index["history.csv"].values():
            _require(_reference(row, "customer") in index["customers.csv"] and
                     _reference(row, "vehicle") in vehicles and
                     _reference(row, "service") in services, "UNRESOLVED_REFERENCE")
            # Counterparty is optional, but a half-populated identity is never valid.
            counterparty = _reference(row, "counterparty")
            _require(counterparty == ("", "") or counterparty in index["counterparties.csv"],
                     "UNRESOLVED_REFERENCE")
        for row in index["documents.csv"].values():
            _require(_reference(row, "history") in index["history.csv"], "UNRESOLVED_REFERENCE")
            _require(SHA.fullmatch(row["document_sha256"]), "DOCUMENT_DIGEST")
        # A document digest is a descriptor, not a verified file or a legal signature.
        # There is intentionally NO linking by VIN, owner creation or DB write.
        return {**BASE_REPORT, "ok": True, "catalogCounts": counts,
                "counts": {TYPES[name]: len(index[name]) for name in RELATION_HEADERS},
                "catalogBundleSha256": catalog_digest,
                "manifestSha256": expected_manifest_sha256,
                "documentPayloadsVerified": False}
    except GraphDenied as error:
        return {**BASE_REPORT, "ok": False, "code": str(error)}
    except ProvenanceDenied as error:
        return {**BASE_REPORT, "ok": False, "code": str(error)}
    except PrepareDenied:
        return {**BASE_REPORT, "ok": False, "code": "INVALID_SOURCE"}
    except CutoverInput:
        return {**BASE_REPORT, "ok": False, "code": "INVALID_CATALOG"}
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError, OSError, csv.Error):
        return {**BASE_REPORT, "ok": False, "code": "SCHEMA"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--synthetic-only", action="store_true", required=True)
    parser.add_argument("--expect-manifest-sha256", required=True)
    parser.add_argument("manifest", type=Path)
    args = parser.parse_args()
    result = inspect_graph(args.manifest, expected_manifest_sha256=args.expect_manifest_sha256)
    print(json.dumps(result, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
