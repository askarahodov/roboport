"""Подготовка искусственного комплекта CSV/XLSX без записи в PostgreSQL."""
import base64
import binascii
import csv
from hashlib import sha256
import io
from zlib import error as ZlibError

from scripts.auto_migration_dry_run import COLUMNS as INVENTORY
from scripts.auto_vehicle_migration_preflight import COLUMNS as VEHICLE
from scripts.auto_synthetic_xlsx import _rows_from_xlsx, SyntheticWorkbookDenied
from .auto_service_import import FIELDS as SERVICE


HEADERS = {"vehicles.csv": VEHICLE, "services.csv": SERVICE, "inventory.csv": INVENTORY}
MAX_FILE = 1_000_000
MAX_ROWS = 2000


class PrepareDenied(ValueError):
    pass


def _mapping(value, headers):
    if value is None:
        return None
    if (type(value) is not dict or len(value) != len(headers)
            or any(type(k) is not str or not 1 <= len(k) <= 128
                   or type(v) is not str for k, v in value.items())
            or set(value.values()) != set(headers)):
        raise PrepareDenied("MAPPING")
    return value


def _csv_rows(raw, headers, mapping):
    reader = csv.reader(io.StringIO(raw.decode("utf-8-sig"), newline=""), strict=True)
    incoming = next(reader, [])
    if len(incoming) != len(headers) or len(set(incoming)) != len(headers):
        raise PrepareDenied("SCHEMA")
    if mapping is None:
        if tuple(incoming) != headers:
            raise PrepareDenied("SCHEMA")
        order = range(len(headers))
    else:
        if set(incoming) != set(mapping):
            raise PrepareDenied("MAPPING")
        order = [incoming.index(next(k for k, v in mapping.items() if v == field))
                 for field in headers]
    yield headers
    for number, row in enumerate(reader, 1):
        if number > MAX_ROWS or len(row) != len(headers):
            raise PrepareDenied("SCHEMA")
        yield [row[i] for i in order]


def _encode(rows):
    # Ограничиваем результат до накопления всего преобразованного XLSX:
    # shared strings могут многократно ссылаться на один большой текст.
    output = bytearray()
    for number, row in enumerate(rows):
        if number > MAX_ROWS or sum(len(cell) for cell in row) > MAX_FILE:
            raise PrepareDenied("INPUT_LIMIT")
        line = io.StringIO(newline="")
        csv.writer(line, lineterminator="\r\n").writerow(row)
        chunk = line.getvalue().encode("utf-8")
        if len(output) + len(chunk) > MAX_FILE:
            raise PrepareDenied("INPUT_LIMIT")
        output.extend(chunk)
    return bytes(output)


def normalize_sources(body):
    """Привести все три источника к каноническим CSV или отказать целиком."""
    if (type(body) is not dict or set(body) != {"syntheticOnly", "files"}
            or body["syntheticOnly"] is not True or type(body["files"]) is not dict
            or set(body["files"]) != set(HEADERS)):
        raise PrepareDenied("SCHEMA")
    result = {}
    for name, headers in HEADERS.items():
        entry = body["files"][name]
        if (type(entry) is not dict or set(entry) != {"base64", "sha256", "format", "mapping"}
                or type(entry["base64"]) is not str
                or len(entry["base64"]) > 4 * ((MAX_FILE + 2) // 3)
                or type(entry["sha256"]) is not str or entry["format"] not in ("CSV", "XLSX")):
            raise PrepareDenied("SCHEMA")
        try:
            raw = base64.b64decode(entry["base64"], validate=True)
            if (not 0 < len(raw) <= MAX_FILE or base64.b64encode(raw).decode() != entry["base64"]
                    or sha256(raw).hexdigest() != entry["sha256"]):
                raise PrepareDenied("SOURCE_MISMATCH")
            mapping = _mapping(entry["mapping"], headers)
            if entry["format"] == "CSV":
                # Уже канонические CSV сохраняют исходную байтовую идентичность.
                rows = _csv_rows(raw, headers, mapping)
                checked = _encode(rows)
                canonical = raw if mapping is None else checked
            else:
                canonical = _encode(_rows_from_xlsx(raw, headers, mapping))
            result[name] = {"base64": base64.b64encode(canonical).decode(),
                            "sha256": sha256(canonical).hexdigest()}
        except PrepareDenied:
            raise
        except (UnicodeError, csv.Error, SyntheticWorkbookDenied, ValueError,
                binascii.Error, RuntimeError, OSError, NotImplementedError, OverflowError, ZlibError):
            raise PrepareDenied("INVALID_SOURCE") from None
    return {"syntheticOnly": True, "files": result}
