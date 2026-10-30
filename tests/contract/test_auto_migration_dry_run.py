"""Offline migration preflight: only synthetic SKU/stock, never PII or writes."""
import csv
from pathlib import Path
from uuid import uuid4

from scripts.auto_migration_dry_run import COLUMNS, inspect_csv


def write(path, records):
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(COLUMNS)
        writer.writerows(records)


def test_synthetic_sku_stock_reconcile_and_repeat(tmp_path):
    org, loc = str(uuid4()), str(uuid4())
    path = tmp_path / "safe.csv"
    write(path, [
        ("SKU", "SKU-1", org, loc, "FILTER-001", "0"),
        ("STOCK", "EVENT-1", org, loc, "FILTER-001", "25"),
    ])
    first = inspect_csv(path)
    assert first == inspect_csv(path)
    assert first == {"ok": True, "counts": {"SKU": 1, "STOCK": 1},
                     "errors": [], "mode": "DRY_RUN_ONLY", "writes": 0}


def test_bad_schema_unknown_stock_duplicate_and_no_pii_leak(tmp_path):
    org, loc = str(uuid4()), str(uuid4())
    path = tmp_path / "unsafe.csv"
    write(path, [
        ("STOCK", "EVENT-1", org, loc, "FILTER-001", "1"),
        ("STOCK", "EVENT-1", org, loc, "FILTER-001", "1"),
        ("SKU", "SKU-1", org, loc, "FILTER-001", "1"),
        ("CUSTOMER", "NAME-1", org, loc, "FILTER-001", "0"),
    ])
    result = inspect_csv(path)
    assert result["ok"] is False
    assert {e["code"] for e in result["errors"]} >= {
        "DUPLICATE_SOURCE", "QUANTITY", "TYPE", "STOCK_WITHOUT_SKU"}
    assert org not in str(result) and loc not in str(result)
    assert "EVENT-1" not in str(result)


def test_unexpected_columns_and_symlink_refused(tmp_path):
    path = tmp_path / "bad.csv"
    path.write_text("record_type,full_vin,customer_name\\nSKU,X,Y\\n",encoding="utf-8")
    assert inspect_csv(path)["errors"][0]["code"] == "SCHEMA"
    link = tmp_path / "linked.csv"
    link.symlink_to(path)
    assert inspect_csv(link)["errors"][0]["code"] == "UNAVAILABLE"


def test_cross_branch_stock_does_not_reconcile(tmp_path):
    org, a, b = str(uuid4()), str(uuid4()), str(uuid4())
    path = tmp_path / "scope.csv"
    write(path, [
        ("SKU","SKU-1",org,a,"FILTER-001","0"),
        ("STOCK","STOCK-1",org,b,"FILTER-001","1"),
    ])
    assert "STOCK_WITHOUT_SKU" in {e["code"] for e in inspect_csv(path)["errors"]}


def test_header_only_csv_is_rejected_without_source_leak(tmp_path):
    path = tmp_path / "empty.csv"
    write(path, [])
    expected = {"ok": False, "counts": {"SKU": 0, "STOCK": 0},
                "errors": [{"row": 0, "code": "EMPTY_INPUT"}],
                "mode": "DRY_RUN_ONLY", "writes": 0}
    assert inspect_csv(path) == expected
    assert inspect_csv(path) == expected


def test_nonempty_invalid_csv_does_not_get_misclassified_as_empty(tmp_path):
    org, loc = str(uuid4()), str(uuid4())
    path = tmp_path / "bad-quantity.csv"
    write(path, [("SKU", "SKU-1", org, loc, "FILTER-001", "-1")])
    result = inspect_csv(path)
    assert result["ok"] is False
    assert result["errors"] == [{"row": 2, "code": "QUANTITY"}]


def test_stock_aggregate_cannot_exceed_limit_within_one_location(tmp_path):
    org, loc = str(uuid4()), str(uuid4())
    path = tmp_path / "overflow.csv"
    write(path, [
        ("SKU", "SKU-1", org, loc, "FILTER-001", "0"),
        ("STOCK", "STOCK-1", org, loc, "FILTER-001", "999999999"),
        ("STOCK", "STOCK-2", org, loc, "FILTER-001", "2"),
    ])
    result = inspect_csv(path)
    assert result["ok"] is False
    assert result["counts"] == {"SKU": 1, "STOCK": 1}
    assert result["errors"] == [{"row": 4, "code": "STOCK_TOTAL_LIMIT"}]
    assert org not in str(result) and loc not in str(result)


def test_stock_aggregate_separated_by_location_and_article(tmp_path):
    org, loc1, loc2 = str(uuid4()), str(uuid4()), str(uuid4())
    path = tmp_path / "scoped.csv"
    write(path, [
        ("SKU", "SKU-1", org, loc1, "FILTER-001", "0"),
        ("SKU", "SKU-2", org, loc2, "FILTER-001", "0"),
        ("SKU", "SKU-3", org, loc1, "FILTER-002", "0"),
        ("STOCK", "STOCK-1", org, loc1, "FILTER-001", "1000000000"),
        ("STOCK", "STOCK-2", org, loc2, "FILTER-001", "1000000000"),
        ("STOCK", "STOCK-3", org, loc1, "FILTER-002", "1000000000"),
    ])
    assert inspect_csv(path) == {"ok": True, "counts": {"SKU": 3, "STOCK": 3},
                                 "errors": [], "mode": "DRY_RUN_ONLY", "writes": 0}


def test_reconciliation_checks_physical_and_accepted_counts(tmp_path):
    org, loc = str(uuid4()), str(uuid4())
    path = tmp_path / "batch.csv"
    write(path, [
        ("SKU", "SKU-1", org, loc, "FILTER-001", "0"),
        ("STOCK", "EVENT-1", org, loc, "FILTER-001", "12"),
    ])
    assert inspect_csv(path, expected_counts={"rows": 2, "SKU": 1, "STOCK": 1})["ok"]
    result = inspect_csv(path, expected_counts={"rows": 3, "SKU": 2, "STOCK": 0})
    assert not result["ok"]
    assert {e["code"] for e in result["errors"]} == {
        "RECONCILIATION_ROWS", "RECONCILIATION_SKU", "RECONCILIATION_STOCK"
    }
    assert org not in str(result) and loc not in str(result)


def test_reconciliation_does_not_count_invalid_rows_as_accepted(tmp_path):
    org, loc = str(uuid4()), str(uuid4())
    path = tmp_path / "invalid.csv"
    write(path, [("SKU", "SKU-1", org, loc, "FILTER-001", "-1")])
    result = inspect_csv(path, expected_counts={"rows": 1, "SKU": 1, "STOCK": 0})
    assert {e["code"] for e in result["errors"]} == {
        "QUANTITY", "RECONCILIATION_SKU"
    }


def test_cli_requires_all_expected_counts(monkeypatch, tmp_path, capsys):
    import json
    import sys
    import pytest
    from scripts.auto_migration_dry_run import main

    org, loc = str(uuid4()), str(uuid4())
    path = tmp_path / "batch.csv"
    write(path, [("SKU", "SKU-1", org, loc, "FILTER-001", "0")])
    monkeypatch.setattr(sys, "argv", ["dry-run", "--synthetic-only",
                                     "--expect-rows", "1", "--expect-sku", "1",
                                     "--expect-stock", "0", str(path)])
    assert main() == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True
    monkeypatch.setattr(sys, "argv", ["dry-run", "--synthetic-only",
                                     "--expect-rows", "1", str(path)])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 2
    monkeypatch.setattr(sys, "argv", ["dry-run", "--synthetic-only",
                                     "--expect-rows", "-1", "--expect-sku", "1",
                                     "--expect-stock", "0", str(path)])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 2


def test_source_checksum_binds_exact_parsed_bytes(tmp_path):
    import hashlib
    org, loc = str(uuid4()), str(uuid4())
    path = tmp_path / "source.csv"
    write(path, [
        ("SKU", "SKU-1", org, loc, "FILTER-001", "0"),
        ("STOCK", "STOCK-1", org, loc, "FILTER-001", "25"),
    ])
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    assert inspect_csv(path, expected_sha256=digest)["ok"] is True
    path.write_bytes(path.read_bytes().replace(b",25", b",26"))
    result = inspect_csv(path, expected_sha256=digest)
    assert result["ok"] is False
    assert result["errors"] == [{"row": 0, "code": "SOURCE_CHECKSUM"}]
    assert "SKU-1" not in str(result) and org not in str(result)


def test_cli_rejects_invalid_source_digest(monkeypatch, tmp_path):
    import pytest
    import sys
    from scripts.auto_migration_dry_run import main
    path = tmp_path / "source.csv"
    write(path, [])
    for value in ("abc", "G" * 64, "-1" * 32):
        monkeypatch.setattr(sys, "argv", [
            "dry-run", "--synthetic-only", "--expect-sha256", value, str(path)
        ])
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 2
