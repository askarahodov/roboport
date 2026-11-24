"""Закрытая репетиция импорта трёх синтетических каталогов через HTTPS."""
import base64
import binascii
import csv
from hashlib import sha256
import io
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import UUID, uuid5, NAMESPACE_URL
from zipfile import ZipFile, ZipInfo, ZIP_STORED

import psycopg
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from .auth_http import _mutation_ok
from .auto_vehicle_claim_http import _request_dsn, _response
from .auto_synthetic_cutover import (
    SyntheticCutoverSources, SyntheticCutoverDenied, _authority,
    rehearse_synthetic_cutover, reconcile_synthetic_cutover,
)
from .auto_synthetic_cutover_export import (
    _synthetic_bundle_counts, export_reconciled_synthetic_cutover,
    verify_synthetic_cutover_export,
)

router = APIRouter(include_in_schema=False)
ROOT = "/api/v1/dev/auto/organizations/{org_id}/locations/{loc_id}/cutover"
FILES = ("vehicles.csv", "services.csv", "inventory.csv")
# Отдельный небольшой лимит браузерного импорта, не лимит offline CLI.
MAX_FILE = 1_000_000
MAX_BODY = 4_100_000
MAX_ROWS = 2000


class CutoverInput(ValueError):
    pass


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise CutoverInput("INVALID_INPUT")
        result[key] = value
    return result


async def _body(request):
    if request.headers.get("content-type", "").split(";")[0].strip() != "application/json":
        raise CutoverInput("INVALID_INPUT")
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw) + len(chunk) > MAX_BODY:
            raise CutoverInput("INPUT_LIMIT")
        raw.extend(chunk)
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=_unique)
    except (UnicodeError, ValueError, RecursionError):
        raise CutoverInput("INVALID_INPUT") from None


def prepare_bundle(body, org, loc, operation):
    """Один строгий формат preview/commit/reconcile/export, без путей пользователя."""
    required = {"syntheticOnly", "files"}
    if operation != "preview":
        required.add("bundleSha256")
    if type(body) is not dict or set(body) != required or body["syntheticOnly"] is not True:
        raise CutoverInput("INVALID_INPUT")
    if type(body["files"]) is not dict or set(body["files"]) != set(FILES):
        raise CutoverInput("INVALID_INPUT")
    raws = {}
    hashes = {}
    for name in FILES:
        entry = body["files"][name]
        if (type(entry) is not dict or set(entry) != {"base64", "sha256"}
                or type(entry["base64"]) is not str
                or len(entry["base64"]) > 4 * ((MAX_FILE + 2) // 3)
                or type(entry["sha256"]) is not str or len(entry["sha256"]) != 64):
            raise CutoverInput("INVALID_INPUT")
        try:
            raw = base64.b64decode(entry["base64"], validate=True)
        except (ValueError, binascii.Error):
            raise CutoverInput("INVALID_INPUT") from None
        if (not 0 < len(raw) <= MAX_FILE or base64.b64encode(raw).decode() != entry["base64"]
                or sha256(raw).hexdigest() != entry["sha256"]):
            raise CutoverInput("SOURCE_MISMATCH")
        raws[name] = raw
        hashes[name] = entry["sha256"]
    try:
        counts = _synthetic_bundle_counts(raws, str(org), str(loc))
        if any(counts[key] > MAX_ROWS for key in ("vehicles", "services", "stockRows")):
            raise CutoverInput("INPUT_LIMIT")
        # Этот интерфейс сознательно принимает лишь явно маркированные фикстуры.
        # Маркер сам по себе не доказывает отсутствие ПДн: реальные файлы запрещены.
        for name, raw in raws.items():
            for row in csv.DictReader(io.StringIO(raw.decode("utf-8-sig"), newline=""), strict=True):
                if name == "vehicles.csv":
                    if (row["make"] != "SYNTHETIC" or not row["model"].startswith("TEST ")
                            or row["source_system"] != "DEMO"
                            or any(c not in "0123456789ABCDEF" for c in row["vin"])):
                        raise CutoverInput("SYNTHETIC_REQUIRED")
                elif name == "services.csv":
                    if row["source_system"] != "DEMO" or not row["title"].startswith("SYNTHETIC "):
                        raise CutoverInput("SYNTHETIC_REQUIRED")
                elif not row["article"].startswith("SYN-"):
                    raise CutoverInput("SYNTHETIC_REQUIRED")
    except SyntheticCutoverDenied:
        raise CutoverInput("INVALID_SOURCE") from None
    # Стабильная идентичность scope + исходных байтов, включая BOM и переносы строк.
    identity = json.dumps({"version":1,"organizationId":str(org),"locationId":str(loc),
                           "files":hashes}, sort_keys=True, separators=(",",":")).encode()
    digest = sha256(identity).hexdigest()
    if operation != "preview" and body["bundleSha256"] != digest:
        raise CutoverInput("SOURCE_MISMATCH")
    batches = {name: uuid5(NAMESPACE_URL, "roboport:synthetic-cutover-ui:v1:"+digest+":"+name)
               for name in FILES}
    return raws, hashes, counts, digest, batches


def _sources(directory, raws, hashes, batches):
    for name in FILES:
        fd = os.open(directory / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(raws[name])
    return SyntheticCutoverSources(
        directory / FILES[0], hashes[FILES[0]], batches[FILES[0]],
        directory / FILES[1], hashes[FILES[1]], batches[FILES[1]],
        directory / FILES[2], hashes[FILES[2]], batches[FILES[2]],
    )


def _zip(directory):
    output = io.BytesIO()
    with ZipFile(output, "w", compression=ZIP_STORED) as archive:
        for name in (*FILES, "manifest.json"):
            entry = ZipInfo(name)
            entry.create_system = 3
            entry.external_attr = 0o100600 << 16
            archive.writestr(entry, (directory / name).read_bytes())
    return output.getvalue()


@router.post(ROOT+"/{operation}")
async def cutover(org_id: str, loc_id: str, operation: str, request: Request):
    dsn = _request_dsn(request)
    if isinstance(dsn, JSONResponse):
        return dsn
    if os.getenv("ROBOport_SYNTHETIC_CUTOVER_UI") != "1":
        return _response(request, 404, "NOT_FOUND")
    if operation not in ("preview", "commit", "reconcile", "export"):
        return _response(request, 404, "NOT_FOUND")
    try:
        org, loc = UUID(org_id), UUID(loc_id)
        body = await _body(request)
    except (ValueError, TypeError):
        return _response(request, 422, "INVALID_INPUT")
    token = request.cookies.get("rp_session", "")
    try:
        with psycopg.connect(dsn) as db:
            if not _mutation_ok(request, db):
                return _response(request, 403, "FORBIDDEN")
            with db.transaction():
                _authority(db, token, org, loc)
                raws, hashes, counts, digest, batches = prepare_bundle(body, org, loc, operation)
                common = {"ok":True, "syntheticOnly":True, "productionApproved":False,
                          "bundleSha256":digest,"counts":counts,
                          "batchIds":{name:str(value) for name,value in batches.items()}}
                if operation == "preview":
                    return JSONResponse(content={**common,"mode":"PREVIEW","writes":0},
                                        headers={"Cache-Control":"no-store"})
                with TemporaryDirectory(prefix="auto-cutover-ui-") as temp:
                    directory = Path(temp)
                    sources = _sources(directory, raws, hashes, batches)
                    args = {"organization_id":org, "location_id":loc,
                            "sources":sources, "synthetic_only":True}
                    if operation == "commit":
                        result = rehearse_synthetic_cutover(db, token, **args)
                    elif operation == "reconcile":
                        result = reconcile_synthetic_cutover(db, token, **args)
                    else:
                        dest = directory / "export"
                        proof = export_reconciled_synthetic_cutover(db, token, destination=dest, **args)
                        verify_synthetic_cutover_export(dest, expected_manifest_sha256=proof["manifestSha256"])
                        raw_zip = _zip(dest)
                        return Response(content=raw_zip,media_type="application/zip",headers={
                            "Cache-Control":"no-store","X-Content-Type-Options":"nosniff",
                            "Content-Disposition":'attachment; filename="synthetic-cutover.zip"',
                            "X-Cutover-SHA256":sha256(raw_zip).hexdigest(),
                            "X-Cutover-Manifest-SHA256":proof["manifestSha256"],
                        })
                    if any(result[key] != value for key,value in counts.items()):
                        raise SyntheticCutoverDenied("RECONCILIATION_FAILED")
                    return JSONResponse(content={**common,"mode":operation.upper(),
                        "writes":result["writes"],"replayed":result.get("replayed",False)},
                        headers={"Cache-Control":"no-store"})
    except CutoverInput:
        return _response(request, 422, "INVALID_SOURCE")
    except ValueError as exc:
        return _response(request, 403 if str(exc)=="FORBIDDEN" else 409,
                         "FORBIDDEN" if str(exc)=="FORBIDDEN" else "CUTOVER_CONFLICT")
    except (psycopg.errors.IntegrityError, psycopg.errors.RaiseException,
            psycopg.errors.SerializationFailure, psycopg.errors.DeadlockDetected,
            psycopg.errors.LockNotAvailable):
        return _response(request, 409, "CUTOVER_CONFLICT")
    except OSError:
        return _response(request, 503, "UNAVAILABLE")
