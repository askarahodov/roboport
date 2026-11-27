"""Семь синтетических доменов, mapping, проверяемые ссылки и fail-closed отчёт."""
import base64
import csv
from hashlib import sha256
import io
import json
from pathlib import Path
import sys

import pytest

from scripts.auto_migration_graph_preflight import ALL_HEADERS, inspect_graph, main
from test_auto_cutover_http_contract import sample, ORG, LOC
from test_auto_synthetic_xlsx import workbook


def make_graph(directory):
    catalog = sample()["files"]
    common = ("DEMO", "SYN-1", str(ORG), str(LOC), "SYN-LEGAL", "SYN-PROOF")
    rows = {
        "customers.csv": [("CUSTOMER", *common)],
        "counterparties.csv": [("COUNTERPARTY", *common)],
        "history.csv": [("REPAIR_HISTORY", *common, "DEMO", "SYN-1",
                         "DEMO", "SYN-1", "DEMO", "SYN-1", "DEMO", "SYN-1")],
        "documents.csv": [("DOCUMENT", *common, "DEMO", "SYN-1", "a"*64)],
    }
    meta = {"schema": 1, "synthetic_only": True, "organization_id": str(ORG),
            "location_id": str(LOC), "sources": {}}
    for name, headers in ALL_HEADERS.items():
        filename = name.upper()
        if name in catalog:
            raw = base64.b64decode(catalog[name]["base64"])
            count = len(list(csv.DictReader(io.StringIO(raw.decode()))))
        else:
            stream = io.StringIO(newline="")
            csv.writer(stream).writerows([headers, *rows[name]])
            raw = stream.getvalue().encode()
            count = len(rows[name])
        (directory / filename).write_bytes(raw)
        meta["sources"][name] = {"file": filename, "format": "CSV", "mapping": None,
                                 "sha256": sha256(raw).hexdigest(), "bytes": len(raw),
                                 "records": count}
    return meta


def run(directory, meta, *, expected=None):
    path = directory / "manifest.json"
    raw = json.dumps(meta).encode()
    path.write_bytes(raw)
    return inspect_graph(path, expected_manifest_sha256=sha256(raw).hexdigest() if expected is None else expected)


def modify(directory, meta, name, transform):
    entry = meta["sources"][name]
    path = directory / entry["file"]
    raw = transform(path.read_bytes())
    path.write_bytes(raw)
    entry.update(sha256=sha256(raw).hexdigest(), bytes=len(raw))


def test_all_domains_mapping_reconciliation_and_repeat(tmp_path):
    meta = make_graph(tmp_path)
    first = run(tmp_path, meta)
    assert first["ok"] and first["writes"] == 0
    assert first["approval"] == "NOT_VERIFIED" and first["release_allowed"] is False
    assert first["documentPayloadsVerified"] is False
    assert first["counts"] == dict.fromkeys(("CUSTOMER","COUNTERPARTY","REPAIR_HISTORY","DOCUMENT"), 1)
    assert first["catalogCounts"] == {"vehicles":1,"services":1,"stockRows":2,"skus":1,"receipts":1}
    assert run(tmp_path, meta) == first
    assert str(ORG) not in str(first) and "SYN-LEGAL" not in str(first)
    for name in ("customers.csv", "counterparties.csv", "history.csv", "documents.csv"):
        entry = meta["sources"][name]
        grid = list(csv.reader(io.StringIO((tmp_path / entry["file"]).read_text())))
        aliases = ["F"+str(i) for i in range(len(grid[0]))]
        target = tmp_path / (name.upper()+".XLSX")
        workbook(target, aliases[::-1], grid[1][::-1])
        raw = target.read_bytes()
        entry.update(file=target.name, format="XLSX", mapping=dict(zip(aliases, grid[0])),
                     sha256=sha256(raw).hexdigest(), bytes=len(raw))
    checked = run(tmp_path, meta)
    assert checked["ok"] and checked["counts"] == first["counts"]
    assert checked["catalogBundleSha256"] == first["catalogBundleSha256"]


@pytest.mark.parametrize("name,old,new,code", [
    ("history.csv", b"DEMO,SYN-1,DEMO,SYN-1,DEMO,SYN-1,DEMO,SYN-1",
     b"DEMO,SYN-UNKNOWN,DEMO,SYN-1,DEMO,SYN-1,DEMO,SYN-1", "UNRESOLVED_REFERENCE"),
    ("history.csv", b"DEMO,SYN-1,DEMO,SYN-1,DEMO,SYN-1,DEMO,SYN-1",
     b"DEMO,SYN-1,DEMO,SYN-UNKNOWN,DEMO,SYN-1,DEMO,SYN-1", "UNRESOLVED_REFERENCE"),
    ("documents.csv", b"DEMO,SYN-1,"+b"a"*64, b"DEMO,SYN-UNKNOWN,"+b"a"*64, "UNRESOLVED_REFERENCE"),
    ("documents.csv", b"a"*64, b"z"*64, "DOCUMENT_DIGEST"),
    ("customers.csv", b"SYN-LEGAL", b"UNAPPROVED", "SYNTHETIC_REQUIRED"),
    ("counterparties.csv", str(LOC).encode(), str(ORG).encode(), "SCOPE"),
])
def test_content_errors_even_with_correct_recomputed_digests(tmp_path,name,old,new,code):
    meta = make_graph(tmp_path)
    modify(tmp_path, meta, name, lambda raw: raw.replace(old,new))
    result = run(tmp_path, meta)
    assert not result["ok"] and result["code"] == code
    assert result["writes"] == 0 and result["release_allowed"] is False
    assert "SYN-UNKNOWN" not in str(result) and str(ORG) not in str(result)


def test_duplicate_count_scope_manifest_and_tamper(tmp_path):
    meta = make_graph(tmp_path)
    assert run(tmp_path, meta, expected="0"*64)["code"] == "MANIFEST_MISMATCH"
    assert run(tmp_path, meta, expected="")["code"] == "MANIFEST_DIGEST_REQUIRED"
    name = "customers.csv"
    modify(tmp_path, meta, name, lambda raw: raw+raw.splitlines(keepends=True)[1])
    assert run(tmp_path, meta)["code"] == "COUNT_MISMATCH"
    meta["sources"][name]["records"] = 2
    assert run(tmp_path, meta)["code"] == "DUPLICATE_SOURCE"
    meta = make_graph(tmp_path)
    (tmp_path / meta["sources"][name]["file"]).write_bytes(b"X")
    assert run(tmp_path, meta)["code"] == "SOURCE_MISMATCH"
    meta = make_graph(tmp_path)
    del meta["sources"]["documents.csv"]
    assert run(tmp_path, meta)["code"] == "SCOPE_INCOMPLETE"


def test_cli_symlink_and_missing_approval(tmp_path,monkeypatch,capsys):
    meta = make_graph(tmp_path)
    result = run(tmp_path, meta)
    path = tmp_path / "manifest.json"
    monkeypatch.setattr(sys,"argv",["graph","--synthetic-only","--expect-manifest-sha256",result["manifestSha256"],str(path)])
    assert main() == 0 and json.loads(capsys.readouterr().out) == result
    entry = meta["sources"]["documents.csv"]
    source = tmp_path / entry["file"]
    original = source.with_suffix(".OLD")
    source.rename(original)
    source.symlink_to(original)
    assert run(tmp_path, meta)["code"] == "UNAVAILABLE"
    meta["synthetic_only"] = False
    assert run(tmp_path, meta)["code"] == "APPROVAL_REQUIRED"


def test_owner_columns_and_formula_are_not_accepted(tmp_path):
    meta = make_graph(tmp_path)
    name = "customers.csv"
    modify(tmp_path,meta,name,lambda raw:raw.replace(b"owner_proof_ref\r\n",b"owner_proof_ref,name\r\n").rstrip(b"\r\n")+b",PERSON\r\n")
    assert run(tmp_path,meta)["code"] == "INVALID_SOURCE"
    meta = make_graph(tmp_path)
    entry = meta["sources"][name]
    rows = list(csv.reader(io.StringIO((tmp_path / entry["file"]).read_text())))
    target = tmp_path / "CUSTOMERS.XLSX"
    workbook(target, rows[0],rows[1],formula=True)
    raw=target.read_bytes()
    entry.update(file=target.name,format="XLSX",sha256=sha256(raw).hexdigest(),bytes=len(raw))
    assert run(tmp_path,meta)["code"] == "INVALID_SOURCE"
