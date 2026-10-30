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
        assert db.execute("""SELECT count(*) FROM auto_stock_event e
                             JOIN auto_stock_sku s ON s.id=e.sku_id
                             WHERE s.organization_id=%s""", (org,)).fetchone()[0] == 0


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


def test_replay_rejects_preexisting_lines_with_matching_counts_but_wrong_content(tmp_path):
    with psycopg.connect(DSN) as db:
        org, loc, _ = _org_and_bay(db)
        batch = uuid4()
        path = tmp_path / "batch.csv"
        digest = _csv(path, [
            ("SKU", "SKU-1", str(org), str(loc), "FILTER-001", "0"),
        ])
        db.execute("""
            INSERT INTO auto_migration_staging_batch
              (id,organization_id,source_sha256,row_count,sku_count,stock_count)
            VALUES (%s,%s,%s,1,1,0)
        """, (batch, org, digest))
        db.execute("""
            INSERT INTO auto_migration_staging_line
              (batch_id,line_number,organization_id,location_id,
               record_type,source_id,article,quantity)
            VALUES (%s,2,%s,%s,'SKU','WRONG-SOURCE','FILTER-001',0)
        """, (batch, org, loc))
        with pytest.raises(SyntheticImportRejected, match="existing staging lines mismatch"):
            stage_synthetic_csv(db, path, organization_id=org, batch_id=batch,
                                expected_sha256=digest)
        assert db.execute("""
            SELECT source_id FROM auto_migration_staging_line WHERE batch_id=%s
        """, (batch,)).fetchone()[0] == "WRONG-SOURCE"


def test_replay_rejects_matching_count_when_line_order_changed(tmp_path):
    with psycopg.connect(DSN) as db:
        org, loc, _ = _org_and_bay(db)
        batch = uuid4()
        path = tmp_path / "batch.csv"
        digest = _csv(path, [
            ("SKU", "SKU-1", str(org), str(loc), "FILTER-001", "0"),
            ("STOCK", "STOCK-1", str(org), str(loc), "FILTER-001", "5"),
        ])
        db.execute("""
            INSERT INTO auto_migration_staging_batch
              (id,organization_id,source_sha256,row_count,sku_count,stock_count)
            VALUES (%s,%s,%s,2,1,1)
        """, (batch, org, digest))
        for line_number, kind, source, quantity in (
            (2, "STOCK", "STOCK-1", 5), (3, "SKU", "SKU-1", 0)
        ):
            db.execute("""
                INSERT INTO auto_migration_staging_line
                  (batch_id,line_number,organization_id,location_id,
                   record_type,source_id,article,quantity)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
            """, (batch, line_number, org, loc, kind, source, "FILTER-001", quantity))
        with pytest.raises(SyntheticImportRejected, match="existing staging lines mismatch"):
            stage_synthetic_csv(db, path, organization_id=org, batch_id=batch,
                                expected_sha256=digest)


def test_staging_rejects_symlinked_source_before_database_write(tmp_path):
    with psycopg.connect(DSN) as db:
        org, loc, _ = _org_and_bay(db)
        batch = uuid4()
        original = tmp_path / "source.csv"
        digest = _csv(original, [
            ("SKU", "SKU-1", str(org), str(loc), "FILTER-001", "0"),
        ])
        link = tmp_path / "indirect.csv"
        link.symlink_to(original)
        with pytest.raises(SyntheticImportRejected, match="source unavailable"):
            stage_synthetic_csv(db, link, organization_id=org, batch_id=batch,
                                expected_sha256=digest)
        assert db.execute(
            "SELECT count(*) FROM auto_migration_staging_batch WHERE id=%s",
            (batch,),
        ).fetchone()[0] == 0


def test_staging_refuses_non_regular_input_without_stalling(tmp_path):
    import os
    import stat
    if not hasattr(os, "mkfifo"):
        pytest.skip("named pipes unavailable on this platform")
    with psycopg.connect(DSN) as db:
        org, _, _ = _org_and_bay(db)
        pipe = tmp_path / "in.fifo"
        os.mkfifo(pipe)
        assert stat.S_ISFIFO(pipe.stat().st_mode)
        # O_NONBLOCK is intentionally not required: regular-file validation
        # must happen before reading and the staging path must fail closed.
        # A symlink to the FIFO is rejected by O_NOFOLLOW.
        link = tmp_path / "to-fifo.csv"
        link.symlink_to(pipe)
        with pytest.raises(SyntheticImportRejected, match="source unavailable"):
            stage_synthetic_csv(db, link, organization_id=org, batch_id=uuid4(),
                                expected_sha256="0" * 64)
