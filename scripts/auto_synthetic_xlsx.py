"""Bounded synthetic XLSX->canonical CSV for vehicle and service import paths.

Offline only. No formulas, macros, external links, hidden sheets or real customer data.
This intentionally supports plain OOXML cells, not a general spreadsheet engine.
"""
from __future__ import annotations

import csv
import hashlib
import io
import os
import re
import stat
import tempfile
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from scripts.auto_vehicle_migration_preflight import COLUMNS as VEHICLE_COLUMNS, inspect_vehicle_csv
from roboport_api.auto_service_import import FIELDS as SERVICE_COLUMNS, inspect_synthetic_services

MAX_ARCHIVE = 5_000_000
MAX_UNPACKED = 12_000_000
MAX_ROWS = 50_000
NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
CELL = re.compile(r"^([A-Z]{1,2})([1-9][0-9]{0,5})$")


class SyntheticWorkbookDenied(ValueError):
    """Stable error code only; never include source values."""


def _column(name: str) -> int:
    n = 0
    for letter in name:
        n = n * 26 + ord(letter) - 64
    return n - 1


def _text(cell, shared: list[str]) -> str:
    if cell.find(NS + "f") is not None:
        raise SyntheticWorkbookDenied("FORMULA")
    kind = cell.get("t", "n")
    if kind == "inlineStr":
        inline = cell.find(NS + "is")
        if inline is None:
            return ""
        return "".join(t.text or "" for t in inline.iter(NS + "t"))
    value = cell.find(NS + "v")
    if value is None or value.text is None:
        return ""
    if kind == "s":
        try:
            index = int(value.text)
            if index < 0:
                raise ValueError()
            return shared[index]
        except (ValueError, IndexError):
            raise SyntheticWorkbookDenied("SCHEMA") from None
    if kind in ("n", "str"):
        return value.text
    raise SyntheticWorkbookDenied("CELL_TYPE")


def _rows_from_xlsx(raw: bytes, headers: tuple[str, ...]) -> list[list[str]]:
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as book:
            members = book.infolist()
            names = {info.filename for info in members}
            if (len(names) != len(members) or len(members) > 80
                    or any(info.file_size > MAX_UNPACKED for info in members)
                    or sum(info.file_size for info in members) > MAX_UNPACKED
                    or any(info.flag_bits & 1 for info in members)
                    or any(info.filename.startswith("/") or ".." in info.filename.split("/")
                           for info in members)
                    or any("externalLink" in name or "vbaProject" in name
                           or "embeddings/" in name or "connections" in name
                           for name in names)):
                raise SyntheticWorkbookDenied("WORKBOOK_UNSAFE")
            required = {"[Content_Types].xml", "xl/workbook.xml",
                        "xl/worksheets/sheet1.xml"}
            if not required.issubset(names):
                raise SyntheticWorkbookDenied("SCHEMA")
            workbook = ET.fromstring(book.read("xl/workbook.xml"))
            sheets = workbook.find(NS + "sheets")
            if (sheets is None or len(sheets) != 1
                    or sheets[0].get("state", "visible") != "visible"):
                raise SyntheticWorkbookDenied("SHEETS")
            shared = []
            if "xl/sharedStrings.xml" in names:
                strings = ET.fromstring(book.read("xl/sharedStrings.xml"))
                shared = ["".join(t.text or "" for t in item.iter(NS + "t"))
                          for item in strings.findall(NS + "si")]
            sheet = ET.fromstring(book.read("xl/worksheets/sheet1.xml"))
            grid = sheet.find(NS + "sheetData")
            if grid is None:
                raise SyntheticWorkbookDenied("SCHEMA")
            result = []
            previous = 0
            for element in grid.findall(NS + "row"):
                row_number = int(element.get("r", "0"))
                if row_number != previous + 1 or row_number > MAX_ROWS + 1:
                    raise SyntheticWorkbookDenied("ROWS")
                previous = row_number
                fields = [""] * len(headers)
                seen = set()
                for cell in element.findall(NS + "c"):
                    match = CELL.fullmatch(cell.get("r", ""))
                    if not match or int(match.group(2)) != row_number:
                        raise SyntheticWorkbookDenied("CELL_REF")
                    index = _column(match.group(1))
                    if index >= len(headers) or index in seen:
                        raise SyntheticWorkbookDenied("SCHEMA")
                    seen.add(index)
                    fields[index] = _text(cell, shared)
                if len(seen) != len(headers):
                    raise SyntheticWorkbookDenied("SCHEMA")
                result.append(fields)
            if not result or tuple(result[0]) != headers or len(result) < 2:
                raise SyntheticWorkbookDenied("SCHEMA")
            return result
    except (zipfile.BadZipFile, ET.ParseError, ValueError, KeyError, OverflowError):
        raise SyntheticWorkbookDenied("UNREADABLE") from None


def convert_synthetic_xlsx(source: Path, target: Path, *, kind: str) -> dict:
    if kind not in ("VEHICLE", "SERVICE"):
        raise SyntheticWorkbookDenied("TYPE")
    if not hasattr(os, "O_NOFOLLOW"):
        raise SyntheticWorkbookDenied("UNAVAILABLE")
    try:
        fd = os.open(source, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise SyntheticWorkbookDenied("UNAVAILABLE")
            raw = stream.read(MAX_ARCHIVE + 1)
        if len(raw) > MAX_ARCHIVE:
            raise SyntheticWorkbookDenied("FILE_LIMIT")
    except OSError:
        raise SyntheticWorkbookDenied("UNAVAILABLE") from None
    headers = VEHICLE_COLUMNS if kind == "VEHICLE" else SERVICE_COLUMNS
    rows = _rows_from_xlsx(raw, headers)
    out = io.StringIO(newline="")
    writer = csv.writer(out, lineterminator="\n")
    writer.writerows(rows)
    canonical = out.getvalue().encode("utf-8")
    if len(canonical) > 5_000_000:
        raise SyntheticWorkbookDenied("FILE_LIMIT")
    digest = hashlib.sha256(canonical).hexdigest()
    with tempfile.TemporaryDirectory(prefix="synthetic-xlsx-") as folder:
        candidate = Path(folder) / "canonical.csv"
        candidate.write_bytes(canonical)
        if kind == "VEHICLE":
            valid = inspect_vehicle_csv(candidate)["ok"]
        else:
            valid = inspect_synthetic_services(candidate, expected_sha256=digest)["ok"]
    if not valid:
        raise SyntheticWorkbookDenied("PREFLIGHT")
    try:
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(canonical)
    except OSError:
        raise SyntheticWorkbookDenied("OUTPUT_UNAVAILABLE") from None
    return {"ok": True, "type": kind, "rows": len(rows) - 1, "sha256": digest,
            "writes": 0, "mode": "SYNTHETIC_XLSX_TO_CSV"}
