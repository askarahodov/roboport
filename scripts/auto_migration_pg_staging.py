"""Explicit synthetic PostgreSQL staging; no writes to production stock tables.

Call only with a disposable PG17 fixture after Alembic upgrade. Input is snapshotted
once, verified, preflighted, then inserted atomically with tenant scope.
"""
from __future__ import annotations

import csv
import hashlib
import io
import os
import stat
import tempfile
from pathlib import Path
from uuid import UUID

from scripts.auto_migration_dry_run import MAX_FILE_BYTES, inspect_csv


class SyntheticImportRejected(ValueError):
    """Deliberately contains no source rows, identifiers, or personal data."""


def stage_synthetic_csv(conn, path: Path, *, organization_id: UUID,
                        batch_id: UUID, expected_sha256: str) -> dict:
    if not isinstance(organization_id, UUID) or not isinstance(batch_id, UUID):
        raise SyntheticImportRejected("invalid staging scope")
    if (not isinstance(expected_sha256, str) or len(expected_sha256) != 64
            or any(c not in "0123456789abcdef" for c in expected_sha256)):
        raise SyntheticImportRejected("invalid source fingerprint")
    # O_NOFOLLOW guards against a file being replaced by a symlink after checks.
    # fstat validates the opened descriptor, not an earlier path lookup.
    try:
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
        if not hasattr(os, "O_NOFOLLOW"):
            raise SyntheticImportRejected("secure source open unavailable")
        descriptor = os.open(path, flags | os.O_NOFOLLOW)
        with os.fdopen(descriptor, "rb") as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                raise SyntheticImportRejected("source unavailable")
            snapshot = handle.read(MAX_FILE_BYTES + 1)
    except OSError:
        raise SyntheticImportRejected("source unavailable") from None
    if len(snapshot) > MAX_FILE_BYTES:
        raise SyntheticImportRejected("source unavailable")
    if hashlib.sha256(snapshot).hexdigest() != expected_sha256:
        raise SyntheticImportRejected("source fingerprint mismatch")

    # Run the existing full preflight against the exact immutable bytes inserted.
    with tempfile.TemporaryDirectory(prefix="synthetic-import-") as folder:
        captured = Path(folder) / "source.csv"
        captured.write_bytes(snapshot)
        try:
            report = inspect_csv(captured, expected_sha256=expected_sha256)
        except (OSError, UnicodeError, csv.Error):
            raise SyntheticImportRejected("source unreadable") from None
    if not report["ok"]:
        raise SyntheticImportRejected("source validation failed")

    rows = list(csv.DictReader(io.StringIO(snapshot.decode("utf-8-sig"), newline="")))
    expected = (len(rows), report["counts"]["SKU"], report["counts"]["STOCK"])
    with conn.transaction():
        conn.execute("""
            INSERT INTO auto_migration_staging_batch
              (id,organization_id,source_sha256,row_count,sku_count,stock_count)
            VALUES (%s,%s,%s,%s,%s,%s)
            ON CONFLICT (id) DO NOTHING
        """, (batch_id, organization_id, expected_sha256, *expected))
        stored = conn.execute("""
            SELECT organization_id,source_sha256,row_count,sku_count,stock_count
            FROM auto_migration_staging_batch WHERE id=%s FOR UPDATE
        """, (batch_id,)).fetchone()
        if stored != (organization_id, expected_sha256, *expected):
            raise SyntheticImportRejected("staging batch conflicts with existing data")
        existing = conn.execute("""
            SELECT line_number,organization_id,location_id,record_type,
                   source_id,article,quantity
            FROM auto_migration_staging_line WHERE batch_id=%s
            ORDER BY line_number
        """, (batch_id,)).fetchall()
        if existing:
            if len(existing) != expected[0]:
                raise SyntheticImportRejected("incomplete existing staging batch")
            for index, (record, row) in enumerate(zip(existing, rows), start=2):
                required = (index, organization_id, UUID(row["location_id"]),
                            row["record_type"], row["source_id"], row["article"],
                            int(row["quantity"]))
                if record != required:
                    raise SyntheticImportRejected("existing staging lines mismatch")
            return {"ok": True, "replayed": True, "counts": report["counts"],
                    "rows": expected[0], "writes": 0, "mode": "SYNTHETIC_STAGING"}

        for line_number, row in enumerate(rows, start=2):
            if UUID(row["organization_id"]) != organization_id:
                raise SyntheticImportRejected("staging tenant mismatch")
            conn.execute("""
                INSERT INTO auto_migration_staging_line
                  (batch_id,line_number,organization_id,location_id,
                   record_type,source_id,article,quantity)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
            """, (batch_id, line_number, organization_id, UUID(row["location_id"]),
                  row["record_type"], row["source_id"], row["article"],
                  int(row["quantity"])))
        verified = conn.execute("""
            SELECT count(*),count(*) FILTER (WHERE record_type='SKU'),
                   count(*) FILTER (WHERE record_type='STOCK')
            FROM auto_migration_staging_line WHERE batch_id=%s
        """, (batch_id,)).fetchone()
        if verified != expected:
            raise SyntheticImportRejected("staging reconciliation mismatch")
    return {"ok": True, "replayed": False, "counts": report["counts"],
            "rows": expected[0], "writes": expected[0] + 1,
            "mode": "SYNTHETIC_STAGING"}
