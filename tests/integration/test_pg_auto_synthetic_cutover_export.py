"""PG17: end-to-end private synthetic export + offline re-verification.

No production/customer/owner data and no use of a privileged HTTP endpoint.
"""
from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path
import stat
from uuid import uuid4

import psycopg
import pytest

from roboport_api.auto_synthetic_cutover_export import (
    export_reconciled_synthetic_cutover, verify_synthetic_cutover_export,
)
from roboport_api.auto_synthetic_cutover import SyntheticCutoverDenied
from test_pg_auto_synthetic_cutover import _setup, _go, _counts
from test_pg_baseline import DSN


def _export(db, d, org, loc, sources, dest, token=None, synthetic_only=True):
    return export_reconciled_synthetic_cutover(
        db,token or d["tokens"][d["intake"]],organization_id=org,
        location_id=loc,sources=sources,destination=dest,synthetic_only=synthetic_only,
    )


def test_full_synthetic_cutover_exports_private_reconciled_package(tmp_path):
    d,org,loc,_,_,_,sources=_setup(tmp_path / "sources")
    destination=tmp_path/"export"
    with psycopg.connect(DSN) as db:
        _go(db,d,org,loc,sources)
        counts=_counts(db,sources)
        result=_export(db,d,org,loc,sources,destination)
        assert result["ok"] and result["files"]==4
        assert result["writes"]==0 and result["productionApproved"] is False
        assert _counts(db,sources)==counts
        assert db.execute("SELECT count(*) FROM auto_vehicle_customer_link link JOIN "
                          "auto_vehicle_migration_promotion p ON p.vehicle_id=link.vehicle_id "
                          "WHERE p.batch_id=%s",(sources.vehicle_batch_id,)).fetchone()[0]==0
    assert stat.S_IMODE(destination.stat().st_mode)==0o700
    manifest=json.loads((destination/"manifest.json").read_bytes())
    assert manifest["kind"]=="SYNTHETIC_CUTOVER_EXPORT"
    assert manifest["productionApproved"] is False
    assert manifest["organizationId"]==str(org) and manifest["locationId"]==str(loc)
    assert manifest["counts"]=={
        "vehicles":1,"services":2,"stockRows":5,"skus":2,"receipts":2,
    }
    for filename, original in (
        ("vehicles.csv",sources.vehicle_path),
        ("services.csv",sources.service_path),
        ("inventory.csv",sources.inventory_path),
    ):
        raw=(destination/filename).read_bytes()
        assert raw==original.read_bytes()
        assert sha256(raw).hexdigest()==manifest["files"][filename]["sha256"]
    assert all(stat.S_IMODE(p.stat().st_mode)==0o600 for p in destination.iterdir())
    checked=verify_synthetic_cutover_export(destination)
    assert checked["ok"] and checked["writes"]==0
    assert checked["counts"]==manifest["counts"]
    assert checked["manifestSha256"]==result["manifestSha256"]
    with psycopg.connect(DSN) as db:
        with pytest.raises(SyntheticCutoverDenied,match="OUTPUT_UNAVAILABLE"):
            _export(db,d,org,loc,sources,destination)
    assert verify_synthetic_cutover_export(destination)["ok"]


def test_export_fails_closed_for_revoked_grant_wrong_scope_and_real_data_flag(tmp_path):
    d,org,loc,_,_,warehouse,sources=_setup(tmp_path/"sources")
    with psycopg.connect(DSN) as db:
        _go(db,d,org,loc,sources)
        with pytest.raises(SyntheticCutoverDenied,match="INVALID_INPUT"):
            _export(db,d,org,loc,sources,tmp_path/"not-synthetic",synthetic_only=False)
        with pytest.raises(SyntheticCutoverDenied,match="SOURCE_UNAVAILABLE"):
            _export(db,d,org,loc,replace(sources,vehicle_sha256="0"*64),
                    tmp_path/"wrong-hash")
        with pytest.raises(SyntheticCutoverDenied,match="FORBIDDEN"):
            _export(db,d,uuid4(),loc,sources,tmp_path/"other-tenant")
        db.execute(
            "UPDATE auto_organization_role_grant SET revoked_at=now() WHERE id=%s",
            (warehouse,),
        )
    with psycopg.connect(DSN) as db:
        with pytest.raises(SyntheticCutoverDenied,match="FORBIDDEN"):
            _export(db,d,org,loc,sources,tmp_path/"revoked")
    for name in ("not-synthetic","wrong-hash","other-tenant","revoked"):
        assert not (tmp_path/name).exists()


def test_export_detects_replaced_sources_and_symlinks_without_new_output(tmp_path):
    d,org,loc,_,_,_,sources=_setup(tmp_path/"sources")
    with psycopg.connect(DSN) as db:
        _go(db,d,org,loc,sources)
    sources.vehicle_path.write_bytes(sources.vehicle_path.read_bytes()+b"\n")
    with psycopg.connect(DSN) as db:
        with pytest.raises(SyntheticCutoverDenied):
            _export(db,d,org,loc,sources,tmp_path/"tampered")
    assert not (tmp_path/"tampered").exists()
    # Restore the exact verified snapshot so the next refusal isolates
    # destination symlink protection rather than an earlier SHA failure.
    sources.vehicle_path.write_bytes(sources.vehicle_path.read_bytes()[:-1])
    # A symlink must not be accepted as the destination, even if its target
    # is empty, and must never overwrite an existing export.
    (tmp_path/"empty").mkdir()
    (tmp_path/"symlink").symlink_to(tmp_path/"empty",target_is_directory=True)
    with psycopg.connect(DSN) as db:
        with pytest.raises(SyntheticCutoverDenied):
            _export(db,d,org,loc,sources,tmp_path/"symlink")
    assert not any((tmp_path/"empty").iterdir())


def test_export_failure_removes_partial_files_and_offline_verifier_rejects_tampering(
    tmp_path,monkeypatch,
):
    from roboport_api import auto_synthetic_cutover_export as module
    d,org,loc,_,_,_,sources=_setup(tmp_path/"sources")
    with psycopg.connect(DSN) as db:
        _go(db,d,org,loc,sources)
        original=module._write_private
        def fail_on_second(directory,name,raw):
            if name=="services.csv":
                raise OSError("simulated disk failure")
            return original(directory,name,raw)
        monkeypatch.setattr(module,"_write_private",fail_on_second)
        with pytest.raises(SyntheticCutoverDenied,match="OUTPUT_UNAVAILABLE"):
            _export(db,d,org,loc,sources,tmp_path/"partial")
        assert not (tmp_path/"partial").exists()
        monkeypatch.setattr(module,"_write_private",original)
        _export(db,d,org,loc,sources,tmp_path/"complete")
    manifest=tmp_path/"complete"/"manifest.json"
    manifest.write_bytes(manifest.read_bytes().replace(b'"version":1',b'"version":2'))
    with pytest.raises(SyntheticCutoverDenied,match="INVALID_EXPORT"):
        verify_synthetic_cutover_export(tmp_path/"complete")
    # Even after restoring manifest, changed source bytes must fail integrity.
    doc=json.loads(manifest.read_bytes())
    doc["version"]=1
    manifest.write_text(json.dumps(doc),encoding="utf-8")
    target=tmp_path/"complete"/"inventory.csv"
    target.write_bytes(target.read_bytes()+b"\n")
    with pytest.raises(SyntheticCutoverDenied,match="INVALID_EXPORT"):
        verify_synthetic_cutover_export(tmp_path/"complete")
