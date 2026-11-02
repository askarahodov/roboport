"""Synthetic-only automotive service catalog: strict CSV preflight and scoped PG17 staging.

Not a work-order, money, customer, or production API.
"""
from __future__ import annotations

import csv
import hashlib
import io
import os
import stat
from pathlib import Path
from uuid import UUID

from roboport_api.auto_org_authority import organization_role_allows

FIELDS = ("record_type", "source_system", "source_id", "code", "title", "duration_minutes")
MAX_BYTES = 5_000_000
MAX_ROWS = 50_000
IDENT = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.")
TITLE = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789 -_.")


class ServiceImportDenied(ValueError):
    """Only generic codes, never raw CSV fields."""


def _load(path: Path, digest: str) -> list[tuple]:
    if (type(digest) is not str or len(digest) != 64
            or any(c not in "0123456789abcdef" for c in digest)
            or not hasattr(os, "O_NOFOLLOW")):
        raise ServiceImportDenied("SOURCE_UNAVAILABLE")
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW
                     | getattr(os, "O_BINARY", 0))
        with os.fdopen(fd, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ServiceImportDenied("SOURCE_UNAVAILABLE")
            raw = stream.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES or hashlib.sha256(raw).hexdigest() != digest:
            raise ServiceImportDenied("SOURCE_UNAVAILABLE")
        reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig"), newline=""),
                                strict=True)
        if reader.fieldnames is None or tuple(reader.fieldnames) != FIELDS:
            raise ServiceImportDenied("SCHEMA")
        seen_source, seen_codes, lines = set(), set(), []
        for number, row in enumerate(reader, 2):
            if number > MAX_ROWS + 1:
                raise ServiceImportDenied("ROW_LIMIT")
            if None in row or any(v is None for v in row.values()):
                raise ServiceImportDenied("SCHEMA")
            if row["record_type"] != "SERVICE":
                raise ServiceImportDenied("TYPE")
            def valid(v, lo, hi, chars):
                return lo <= len(v) <= hi and all(c in chars for c in v)
            if not (valid(row["source_system"], 3, 32, IDENT)
                    and valid(row["source_id"], 1, 64, IDENT)
                    and valid(row["code"], 3, 64, IDENT)):
                raise ServiceImportDenied("IDENTITY")
            title = row["title"]
            if not (valid(title, 1, 120, TITLE)
                    and title == title.strip() and title[0] not in "=+-@"):
                raise ServiceImportDenied("TITLE")
            duration = row["duration_minutes"]
            if not duration.isascii() or not duration.isdecimal():
                raise ServiceImportDenied("DURATION")
            minutes = int(duration)
            if not 5 <= minutes <= 1440 or str(minutes) != duration:
                raise ServiceImportDenied("DURATION")
            source = (row["source_system"], row["source_id"])
            if source in seen_source or row["code"] in seen_codes:
                raise ServiceImportDenied("DUPLICATE")
            seen_source.add(source)
            seen_codes.add(row["code"])
            lines.append((number, *source, row["code"], title, minutes))
        if not lines:
            raise ServiceImportDenied("EMPTY")
        return lines
    except (OSError, UnicodeError, csv.Error):
        raise ServiceImportDenied("SOURCE_UNAVAILABLE") from None


def inspect_synthetic_services(path: Path, *, expected_sha256: str) -> dict:
    try:
        lines = _load(path, expected_sha256)
        return {"ok": True, "count": len(lines), "writes": 0,
                "mode": "SYNTHETIC_SERVICE_PREFLIGHT"}
    except ServiceImportDenied as error:
        return {"ok": False, "code": str(error), "writes": 0,
                "mode": "SYNTHETIC_SERVICE_PREFLIGHT"}


def stage_synthetic_services(conn, token: str, path: Path, *,
                             organization_id: UUID, location_id: UUID,
                             batch_id: UUID, expected_sha256: str) -> dict:
    if (type(token) is not str or not token or
            any(type(value) is not UUID for value in
                (organization_id, location_id, batch_id))):
        raise ServiceImportDenied("FORBIDDEN")
    with conn.transaction():
        if not organization_role_allows(conn, token, organization_id, "MANAGER",
                                        location_id, for_mutation=True):
            raise ServiceImportDenied("FORBIDDEN")
        lines = _load(path, expected_sha256)
        conn.execute("""
            INSERT INTO auto_service_import_batch(
              id,organization_id,location_id,source_sha256,row_count)
            VALUES(%s,%s,%s,%s,%s) ON CONFLICT(id) DO NOTHING
        """, (batch_id, organization_id, location_id, expected_sha256, len(lines)))
        batch = conn.execute("""
            SELECT organization_id,location_id,source_sha256,row_count
            FROM auto_service_import_batch WHERE id=%s FOR UPDATE
        """, (batch_id,)).fetchone()
        if batch != (organization_id, location_id, expected_sha256, len(lines)):
            raise ServiceImportDenied("BATCH_CONFLICT")
        stored = conn.execute("""
            SELECT row_number,source_system,source_id,code,title,duration_minutes
            FROM auto_service_import_line WHERE batch_id=%s ORDER BY row_number
        """, (batch_id,)).fetchall()
        if stored:
            if stored != lines:
                raise ServiceImportDenied("LINE_CONFLICT")
            return {"ok": True, "replayed": True, "count": len(lines),
                    "writes": 0, "mode": "SYNTHETIC_SERVICE_STAGING"}
        for number, system, source, code, title, duration in lines:
            conn.execute("""
                INSERT INTO auto_service_import_line(
                  batch_id,row_number,source_system,source_id,code,title,duration_minutes)
                VALUES(%s,%s,%s,%s,%s,%s,%s)
            """, (batch_id, number, system, source, code, title, duration))
    return {"ok": True, "replayed": False, "count": len(lines),
            "writes": len(lines)+1, "mode": "SYNTHETIC_SERVICE_STAGING"}
