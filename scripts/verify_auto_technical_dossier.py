"""Offline integrity checks for a synthetic closed automotive technical dossier.

The archive is *not* a signed legal document. Internal SHA-256 hashes do
not authenticate a sender: an attacker could replace the entire archive.
"""
from __future__ import annotations

import argparse
import binascii
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import re
import struct
import sys
import zlib
from uuid import UUID
from zipfile import BadZipFile, ZipFile, ZIP_STORED

MAX_ARCHIVE_BYTES = 1024 * 1024
MAX_PNG_BYTES = 196608
MAX_PNG_PIXELS = 262144
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
TOP_FILES = ("manifest.json", "preview.json", "document-events.json",
             "settlement.json", "intake-evidence.zip")
HEXDIGEST = re.compile(r"[0-9a-f]{64}\Z")
PHOTO = re.compile(r"photos/[1-3]\.png\Z")


class DossierVerificationError(ValueError):
    """A locally supplied archive failed bounded, fail-closed validation."""


def _require(value: bool, code: str) -> None:
    if not value:
        raise DossierVerificationError(code)


def _uuid(value: object) -> bool:
    try:
        return type(value) is str and str(UUID(value)) == value
    except (ValueError, AttributeError, TypeError):
        return False


def _hash(value: object) -> bool:
    return type(value) is str and HEXDIGEST.fullmatch(value) is not None


def _integer(value: object) -> bool:
    return type(value) is int and value >= 0


def _no_duplicates(pairs):
    obj = {}
    for key, value in pairs:
        _require(key not in obj, "DUPLICATE_JSON_KEY")
        obj[key] = value
    return obj


def _json(blob: bytes, code: str):
    try:
        result = json.loads(blob.decode("utf-8"), object_pairs_hook=_no_duplicates)
    except (UnicodeDecodeError, ValueError, TypeError, RecursionError) as exc:
        if isinstance(exc, DossierVerificationError):
            raise
        raise DossierVerificationError(code) from None
    _require(type(result) in (dict, list), code)
    return result


def _read_zip(raw: bytes, expected: tuple[str, ...], code: str) -> dict[str, bytes]:
    _require(type(raw) is bytes and len(raw) <= MAX_ARCHIVE_BYTES, "ARCHIVE_TOO_LARGE")
    try:
        with ZipFile(BytesIO(raw)) as archive:
            entries = archive.infolist()
            names = tuple(e.filename for e in entries)
            _require(len(entries) == len(expected) and names == expected, code)
            _require(len(set(names)) == len(names), code)
            _require(all(e.compress_type == ZIP_STORED and not e.flag_bits & 1
                         and e.file_size <= MAX_ARCHIVE_BYTES
                         and e.compress_size == e.file_size for e in entries), code)
            _require(sum(e.file_size for e in entries) <= MAX_ARCHIVE_BYTES, code)
            return {e.filename: archive.read(e) for e in entries}
    except (BadZipFile, EOFError, RuntimeError, OSError, NotImplementedError,
            OverflowError) as exc:
        raise DossierVerificationError(code) from None


def _manifest_file_hashes(manifest: dict, archive: dict[str, bytes]) -> None:
    files = manifest.get("files")
    _require(type(files) is list and len(files) == 4, "INVALID_MANIFEST")
    _require(tuple(entry.get("path") for entry in files if type(entry) is dict)
             == TOP_FILES[1:], "INVALID_MANIFEST")
    for entry in files:
        payload = archive[entry["path"]]
        _require(set(entry) == {"path", "bytes", "sha256"}
                 and _integer(entry["bytes"]) and entry["bytes"] == len(payload)
                 and _hash(entry["sha256"])
                 and sha256(payload).hexdigest() == entry["sha256"],
                 "FILE_DIGEST_MISMATCH")


def _verify_sanitized_png(raw: bytes, width: object, height: object) -> None:
    """Check exact sanitized PNG structure, not merely its signature or SHA.

    The authorized exporter strips all ancillary chunks and writes exactly
    IHDR, one IDAT and IEND. Decompression is bounded like the PG17 writer:
    even a new archive with every file digest recomputed must reject invalid
    pixel evidence.
    """
    _require(type(raw) is bytes and 0 < len(raw) <= MAX_PNG_BYTES and
             raw.startswith(PNG_SIGNATURE), "INVALID_PHOTO_PNG")
    pos = len(PNG_SIGNATURE)
    chunks = {}
    for expected_kind in (b"IHDR", b"IDAT", b"IEND"):
        _require(len(raw) - pos >= 12, "INVALID_PHOTO_PNG")
        length = int.from_bytes(raw[pos:pos + 4], "big")
        kind = raw[pos + 4:pos + 8]
        end = pos + 12 + length
        _require(kind == expected_kind and length <= MAX_PNG_BYTES and
                 end <= len(raw), "INVALID_PHOTO_PNG")
        payload = raw[pos + 8:pos + 8 + length]
        crc = int.from_bytes(raw[pos + 8 + length:end], "big")
        _require((binascii.crc32(kind + payload) & 0xffffffff) == crc,
                 "INVALID_PHOTO_PNG")
        chunks[kind] = payload
        pos = end
    _require(pos == len(raw) and len(chunks[b"IHDR"]) == 13 and
             len(chunks[b"IDAT"]) > 0 and chunks[b"IEND"] == b"",
             "INVALID_PHOTO_PNG")
    png_width, png_height, depth, color, compression, flt, interlace = (
        struct.unpack(">IIBBBBB", chunks[b"IHDR"]))
    _require(1 <= png_width <= 1024 and 1 <= png_height <= 1024 and
             png_width * png_height <= MAX_PNG_PIXELS and
             type(width) is int and width == png_width and
             type(height) is int and height == png_height and
             depth == 8 and color in (2, 6) and
             compression == 0 and flt == 0 and interlace == 0,
             "PHOTO_METADATA_MISMATCH")
    row_size = png_width * (3 if color == 2 else 4) + 1
    decoded_size = row_size * png_height
    try:
        decoder = zlib.decompressobj()
        scanlines = decoder.decompress(chunks[b"IDAT"], decoded_size + 1)
        scanlines += decoder.flush(decoded_size + 1 - len(scanlines))
    except (zlib.error, ValueError):
        raise DossierVerificationError("INVALID_PHOTO_PNG") from None
    _require(len(scanlines) == decoded_size and decoder.eof and
             not decoder.unused_data and not decoder.unconsumed_tail and
             all(scanlines[row * row_size] <= 4 for row in range(png_height)),
             "INVALID_PHOTO_PNG")


def verify_dossier_bytes(data: bytes, *, expected_sha256: str | None = None) -> dict:
    """Validate bounded ZIP structure, nested photos and cross-file invariants.

    No network or database access. PostgreSQL jsonb fingerprints cannot be
    recomputed reliably offline, but all recorded IDs/digests must agree.
    """
    if expected_sha256 is not None:
        _require(_hash(expected_sha256), "INVALID_EXPECTED_SHA256")
        _require(sha256(data).hexdigest() == expected_sha256,
                 "ARCHIVE_SHA256_MISMATCH")
    files = _read_zip(data, TOP_FILES, "INVALID_DOSSIER_ZIP")
    manifest = _json(files["manifest.json"], "INVALID_MANIFEST")
    _require(type(manifest) is dict and set(manifest) == {
        "schemaVersion", "syntheticOnly", "releaseAllowed", "legalSignatureStatus",
        "physicalHandover", "documentStatus", "organizationId", "locationId",
        "workOrderId", "previewId", "previewDigest", "intakeActId",
        "intakeDigest", "files",
    }, "INVALID_MANIFEST")
    _require(type(manifest["schemaVersion"]) is int and manifest["schemaVersion"] == 1
             and manifest["syntheticOnly"] is True
             and manifest["releaseAllowed"] is False
             and manifest["physicalHandover"] is False
             and manifest["legalSignatureStatus"] == "NOT_IMPLEMENTED"
             and manifest["documentStatus"] == "CLOSED_TECHNICAL"
             and all(_uuid(manifest[key]) for key in
                     ("organizationId", "locationId", "workOrderId", "previewId", "intakeActId"))
             and _hash(manifest["previewDigest"]) and _hash(manifest["intakeDigest"]),
             "INVALID_MANIFEST")
    _manifest_file_hashes(manifest, files)

    preview = _json(files["preview.json"], "INVALID_PREVIEW")
    _require(type(preview) is dict and
             preview.get("kind") == "SYNTHETIC_PRE_HANDOVER_PREVIEW" and
             preview.get("workOrderId") == manifest["workOrderId"] and
             preview.get("currency") == "RUB" and
             preview.get("releaseAllowed") is False and
             preview.get("legalGatePassed") is False and
             preview.get("independentQc") == "PASS" and
             preview.get("stockReservations") == 0 and
             preview.get("unreceivedMaterials") == 0 and
             _uuid(preview.get("estimateRevisionId")), "INVALID_PREVIEW")
    fields = ("approvedBaseMinor", "approvedExtraMinor", "approvedTotalMinor",
              "simulatedPaidMinor", "simulatedRefundedMinor", "simulatedDueMinor")
    _require(all(_integer(preview.get(field)) for field in fields), "INVALID_PREVIEW")
    _require(preview["approvedTotalMinor"] ==
             preview["approvedBaseMinor"] + preview["approvedExtraMinor"] and
             preview["simulatedDueMinor"] == 0 and
             preview["simulatedRefundedMinor"] <= preview["simulatedPaidMinor"] and
             preview["approvedTotalMinor"] - preview["simulatedPaidMinor"] +
             preview["simulatedRefundedMinor"] == 0, "INVALID_SETTLEMENT")

    # Verify priced extra work against its immutable line items, not only totals.
    extra_lines = preview.get("extraLines")
    _require(type(extra_lines) is list and len(extra_lines) <= 20
             and (preview["approvedExtraMinor"] == 0 or len(extra_lines) > 0)
             and all(type(line) is dict and _integer(line.get("amountMinor"))
                     for line in extra_lines)
             and sum(line["amountMinor"] for line in extra_lines) ==
             preview["approvedExtraMinor"], "EXTRA_LINES_MISMATCH")

    events = _json(files["document-events.json"], "INVALID_DOCUMENT_EVENTS")
    _require(type(events) is list and len(events) == 3 and
             [e.get("action") for e in events if type(e) is dict] ==
             ["POSTED", "CLIENT_ACK", "CLOSED_TECHNICAL"],
             "INVALID_DOCUMENT_EVENTS")
    for event in events:
        _require(event.get("previewId") == manifest["previewId"] and
                 event.get("previewDigest") == manifest["previewDigest"] and
                 event.get("intakeActId") == manifest["intakeActId"] and
                 event.get("intakeDigest") == manifest["intakeDigest"] and
                 _uuid(event.get("actorId")) and _uuid(event.get("id")),
                 "DOCUMENT_CHAIN_MISMATCH")

    settlement = _json(files["settlement.json"], "INVALID_SETTLEMENT")
    _require(type(settlement) is dict and settlement.get("syntheticOnly") is True
             and settlement.get("currency") == "RUB" and
             settlement.get("approvedRevisionId") == preview["estimateRevisionId"]
             and settlement.get("executionStatus") == "QC_ACCEPTED" and
             settlement.get("outstandingReserves") == [] and
             settlement.get("unreceivedMaterialCount") == 0 and
             all(settlement.get(field) == preview[field] for field in fields),
             "INVALID_SETTLEMENT")
    cash = settlement.get("simulatedCashEvents")
    _require(type(cash) is list and len(cash) <= 100, "INVALID_SETTLEMENT")
    paid = refunded = 0
    for event in cash:
        _require(type(event) is dict and _uuid(event.get("id")) and
                 _integer(event.get("amountMinor")) and event["amountMinor"] > 0,
                 "INVALID_SETTLEMENT")
        if event.get("kind") == "SIM_PAYMENT":
            paid += event["amountMinor"]
        elif event.get("kind") == "SIM_REFUND":
            refunded += event["amountMinor"]
        else:
            raise DossierVerificationError("INVALID_SETTLEMENT")
    _require(paid == settlement["simulatedPaidMinor"] and
             refunded == settlement["simulatedRefundedMinor"], "INVALID_SETTLEMENT")

    nested_raw = files["intake-evidence.zip"]
    _require(len(nested_raw) <= MAX_ARCHIVE_BYTES, "ARCHIVE_TOO_LARGE")
    try:
        with ZipFile(BytesIO(nested_raw)) as nested:
            names = tuple(item.filename for item in nested.infolist())
    except (BadZipFile, EOFError, OSError) as exc:
        raise DossierVerificationError("INVALID_INTAKE_ZIP") from None
    _require(names and names[0] == "manifest.json" and len(names) <= 4 and
             all(PHOTO.fullmatch(name) for name in names[1:]) and
             names[1:] == tuple(f"photos/{n}.png" for n in range(1, len(names))),
             "INVALID_INTAKE_ZIP")
    nested = _read_zip(nested_raw, names, "INVALID_INTAKE_ZIP")
    intake = _json(nested["manifest.json"], "INVALID_INTAKE_MANIFEST")
    _require(type(intake) is dict and intake.get("schemaVersion") == 1 and
             intake.get("syntheticOnly") is True and
             intake.get("releaseAllowed") is False and
             intake.get("legalSignatureStatus") == "NOT_IMPLEMENTED" and
             intake.get("organizationId") == manifest["organizationId"] and
             intake.get("locationId") == manifest["locationId"] and
             intake.get("orderId") == manifest["workOrderId"],
             "INVALID_INTAKE_MANIFEST")
    act = intake.get("act")
    _require(type(act) is dict and act.get("id") == manifest["intakeActId"] and
             act.get("sha256") == manifest["intakeDigest"] and
             type(act.get("review")) is dict and
             act["review"].get("decision") == "ACKNOWLEDGED",
             "INTAKE_CHAIN_MISMATCH")
    photos = intake.get("files")
    _require(type(photos) is list and len(photos) == len(names) - 1,
             "INVALID_INTAKE_MANIFEST")
    # Preserve the independently recorded photo set inside the act itself.
    # A matching nested file list cannot silently omit an act photograph.
    act_photos = act.get("photos")
    _require(type(act_photos) is list and len(act_photos) == len(photos),
             "PHOTO_METADATA_MISMATCH")
    for name, entry, source in zip(names[1:], photos, act_photos, strict=True):
        _require(type(source) is dict and type(entry) is dict and
                 all(source.get(field) == entry.get(field)
                     for field in ("sha256", "areaCode", "width", "height")),
                 "PHOTO_METADATA_MISMATCH")
        _require(type(entry) is dict and entry.get("path") == name and
                 _hash(entry.get("sha256")) and
                 sha256(nested[name]).hexdigest() == entry["sha256"] and
                 nested[name].startswith(PNG_SIGNATURE),
                 "PHOTO_DIGEST_MISMATCH")
        _verify_sanitized_png(nested[name], entry.get("width"),
                              entry.get("height"))
    return {"verified": True, "schemaVersion": 1, "documentStatus": "CLOSED_TECHNICAL",
            "syntheticOnly": True, "releaseAllowed": False,
            "documentEvents": len(events), "photos": len(photos)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Проверить синтетический ZIP закрытого заказ-наряда")
    parser.add_argument("archive", type=Path, help="Локальный технический ZIP (до 1 МиБ)")
    parser.add_argument("--json", action="store_true", help="Машинно-читаемый результат без ПДн")
    parser.add_argument("--expected-sha256", metavar="64_HEX",
                        help="Ранее отдельно сохранённый SHA-256 из авторизованного HTTPS-ответа")
    args = parser.parse_args(argv)
    try:
        if args.archive.stat().st_size > MAX_ARCHIVE_BYTES:
            raise DossierVerificationError("ARCHIVE_TOO_LARGE")
        report = verify_dossier_bytes(args.archive.read_bytes(),
                                      expected_sha256=args.expected_sha256)
    except (OSError, DossierVerificationError) as exc:
        code = str(exc) if isinstance(exc, DossierVerificationError) else "FILE_READ_FAILED"
        if args.json:
            print(json.dumps({"verified": False, "error": code}))
        else:
            print(f"FAIL: {code}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(report, sort_keys=True))
    else:
        print(f"PASS: technical ZIP, {report['documentEvents']} events, "
              f"{report['photos']} photos; NOT a legal signature")
    return 0


if __name__ == "__main__":
    sys.exit(main())
