"""Synthetic PG17 staging: atomicity, exact replay and tenant isolation."""
import csv
import hashlib
from uuid import uuid4

import psycopg
import pytest

from scripts.auto_migration_pg_staging import SyntheticImportRejected, stage_synthetic_csv
from scripts.auto_migration_dry_run import COLUMNS
from test_pg_baseline import DSN
from test_pg_auto_bay_booking import _org_and_bay


def _csv(path, records):
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(COLUMNS)
        writer.writerows(records)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_synthetic_staging_replay_is_idempotent(tmp_path):
    with psycopg.connect(DSN) as db:
        org, loc, _ = _org_and_bay(db)
        batch = uuid4()
        path = tmp_path / "batch.csv"
        digest = _csv(path, [
            ("STOCK", "EVENT-1", str(org), str(loc), "FILTER-001", "3"),
            ("SKU", "SKU-1", str(org), str(loc), "FILTER-001", "0"),
        ])
        first = stage_synthetic_csv(
            db, path, organization_id=org, batch_id=batch, expected_sha256=digest
        )
        again = stage_synthetic_csv(
            db, path, organization_id=org, batch_id=batch, expected_sha256=digest
        )
        assert first == {"ok": True, "replayed": False, "counts": {"SKU": 1, "STOCK": 1},
                         "rows": 2, "writes": 3, "mode": "SYNTHETIC_STAGING"}
        assert again["replayed"] is True and again["writes"] == 0
        assert db.execute("SELECT count(*) FROM auto_migration_staging_line WHERE batch_id=%s",
                          (batch,)).fetchone()[0] == 2
        assert db.execute("SELECT count(*) FROM auto_stock_event").fetchone()[0] == 0


def test_synthetic_staging_rejects_tamper_and_scope_without_partial_writes(tmp_path):
    with psycopg.connect(DSN) as db:
        org, loc, _ = _org_and_bay(db)
        other, _, _ = _org_and_bay(db)
        batch = uuid4()
        path = tmp_path / "batch.csv"
        digest = _csv(path, [
            ("SKU", "SKU-1", str(org), str(loc), "FILTER-001", "0"),
        ])
        with pytest.raises(SyntheticImportRejected):
            stage_synthetic_csv(db, path, organization_id=other, batch_id=batch,
                                expected_sha256=digest)
        assert db.execute("SELECT count(*) FROM auto_migration_staging_batch WHERE id=%s",
                          (batch,)).fetchone()[0] == 0
        path.write_bytes(path.read_bytes() + b"\n")
        with pytest.raises(SyntheticImportRejected):
            stage_synthetic_csv(db, path, organization_id=org, batch_id=batch,
                                expected_sha256=digest)
        assert db.execute("SELECT count(*) FROM auto_migration_staging_batch WHERE id=%s",
                          (batch,)).fetchone()[0] == 0


def test_synthetic_staging_transaction_rollback_and_conflicting_replay(tmp_path):
    with psycopg.connect(DSN) as db:
        org, loc, _ = _org_and_bay(db)
        batch = uuid4()
        path = tmp_path / "batch.csv"
        digest = _csv(path, [
            ("SKU", "SKU-1", str(org), str(loc), "FILTER-001", "0"),
        ])
        with pytest.raises(RuntimeError):
            with db.transaction():
                stage_synthetic_csv(db, path, organization_id=org, batch_id=batch,
                                    expected_sha256=digest)
                raise RuntimeError("rollback")
        assert db.execute("SELECT count(*) FROM auto_migration_staging_batch WHERE id=%s",
                          (batch,)).fetchone()[0] == 0
        stage_synthetic_csv(db, path, organization_id=org, batch_id=batch,
                            expected_sha256=digest)
        changed = _csv(path, [
            ("SKU", "SKU-2", str(org), str(loc), "FILTER-001", "0"),
        ])
        with pytest.raises(SyntheticImportRejected):
            stage_synthetic_csv(db, path, organization_id=org, batch_id=batch,
                                expected_sha256=changed)
        assert db.execute(
            "SELECT source_id FROM auto_migration_staging_line WHERE batch_id=%s",
            (batch,),
        ).fetchone()[0] == "SKU-1"
