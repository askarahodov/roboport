"""Offline cutover transfer verification; no PostgreSQL fixtures or network."""
import csv
from hashlib import sha256
import io
import json
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

import pytest

from roboport_api.auto_synthetic_cutover import SyntheticCutoverDenied
from roboport_api.auto_synthetic_cutover_export import verify_synthetic_cutover_export
from scripts.auto_vehicle_migration_preflight import COLUMNS as VEHICLE_FIELDS
from roboport_api.auto_service_import import FIELDS as SERVICE_FIELDS
from scripts.auto_migration_dry_run import COLUMNS as INVENTORY_FIELDS


def _bundle(tmp_path):
    bundle = tmp_path / "bundle"
    bundle.mkdir(mode=0o700)
    org, loc = str(uuid4()), str(uuid4())
    vin = uuid4().hex[:17].upper()
    files = {}
    for name, fields, rows in (
        ("vehicles.csv", VEHICLE_FIELDS,
         [("VEHICLE","SYN-CAR-1",org,loc,vin,"SYNTHETIC","TEST CAR","DEMO")]),
        ("services.csv", SERVICE_FIELDS,
         [("SERVICE","DEMO","LABOR-1","BRAKE-REPAIR","SYNTHETIC SERVICE",60),
          ("SERVICE","DEMO","LABOR-2","CHECK-COOLANT","SYNTHETIC CHECK",30)]),
        ("inventory.csv", INVENTORY_FIELDS,
         [("STOCK","STOCK-1",org,loc,"FILTER-001",3),
          ("SKU","SKU-1",org,loc,"FILTER-001",0),
          ("SKU","SKU-2",org,loc,"COOLANT-001",0),
          ("STOCK","STOCK-2",org,loc,"FILTER-001",0),
          ("STOCK","STOCK-3",org,loc,"COOLANT-001",5)]),
    ):
        output=io.StringIO(newline="")
        writer=csv.writer(output,lineterminator="\n")
        writer.writerow(fields)
        writer.writerows(rows)
        raw=output.getvalue().encode("utf-8")
        path=bundle/name
        path.write_bytes(raw)
        path.chmod(0o600)
        files[name]={"sha256":sha256(raw).hexdigest(),"bytes":len(raw),
                     "batchId":str(uuid4())}
    manifest={"version":1,"kind":"SYNTHETIC_CUTOVER_EXPORT",
              "productionApproved":False,"organizationId":org,"locationId":loc,
              "counts":{"vehicles":1,"services":2,"stockRows":5,"skus":2,"receipts":2},
              "files":files}
    path=bundle/"manifest.json"
    path.write_text(json.dumps(manifest,sort_keys=True,separators=(",",":"))+"\n",
                    encoding="utf-8")
    path.chmod(0o600)
    return bundle, sha256(path.read_bytes()).hexdigest()


def _forge_bundle(destination: Path, name: str, mutate):
    """Recompute the attacker's own SHA, proving hashes alone are insufficient."""
    filename = destination / name
    raw = filename.read_bytes().decode("utf-8")
    reader = csv.DictReader(io.StringIO(raw, newline=""), strict=True)
    fields = reader.fieldnames
    rows = list(reader)
    mutate(rows)
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    forged = output.getvalue().encode("utf-8")
    filename.write_bytes(forged)
    manifest_path = destination / "manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    manifest["files"][name]["sha256"] = sha256(forged).hexdigest()
    manifest["files"][name]["bytes"] = len(forged)
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )


@pytest.mark.parametrize(("filename","attack"), [
    ("vehicles.csv","cross_tenant"),
    ("vehicles.csv","duplicate_vin"),
    ("vehicles.csv","illegal_vin"),
    ("vehicles.csv","owner_field"),
    ("services.csv","invalid_duration"),
    ("services.csv","duplicate_source"),
    ("inventory.csv","cross_branch"),
    ("inventory.csv","missing_sku"),
    ("inventory.csv","negative_stock"),
])
def test_self_signed_but_semantically_invalid_bundle_is_denied(
    tmp_path,filename,attack,
):
    bundle, _ = _bundle(tmp_path)

    def mutate(rows):
        if attack=="cross_tenant":
            rows[0]["organization_id"]=str(uuid4())
        elif attack=="duplicate_vin":
            rows.append(dict(rows[0],source_id="SYN-CAR-2"))
        elif attack=="illegal_vin":
            rows[0]["vin"]="I"*17
        elif attack=="owner_field":
            rows[0]["vin"]="=HYPERLINK()"
        elif attack=="invalid_duration":
            rows[0]["duration_minutes"]="9999"
        elif attack=="duplicate_source":
            rows[1]["source_id"]=rows[0]["source_id"]
        elif attack=="cross_branch":
            rows[0]["location_id"]=str(uuid4())
        elif attack=="missing_sku":
            rows[0]["article"]="UNKNOWN-SKU"
        elif attack=="negative_stock":
            rows[0]["quantity"]="-1"
    _forge_bundle(bundle,filename,mutate)
    with pytest.raises(SyntheticCutoverDenied,match="INVALID_EXPORT"):
        verify_synthetic_cutover_export(bundle, structure_only=True)


def test_manifest_self_consistent_count_and_scope_forgery_rejected(tmp_path):
    bundle, trusted = _bundle(tmp_path)
    p=bundle/"manifest.json"
    original=json.loads(p.read_bytes())
    for patch in (
        {"counts":{**original["counts"],"vehicles":2}},
        {"locationId":str(uuid4())},
        {"files":{**original["files"],"services.csv":{
            **original["files"]["services.csv"],
            "batchId":original["files"]["vehicles.csv"]["batchId"],
        }}},
    ):
        p.write_text(json.dumps({**original,**patch}),encoding="utf-8")
        with pytest.raises(SyntheticCutoverDenied,match="INVALID_EXPORT"):
            verify_synthetic_cutover_export(bundle, structure_only=True)


def test_valid_substitution_fails_trusted_digest_but_structure_is_explicit(tmp_path):
    bundle, trusted = _bundle(tmp_path)
    result=verify_synthetic_cutover_export(bundle, expected_manifest_sha256=trusted)
    assert result["manifestDigestVerified"] is True
    assert result["productionApproved"] is False and result["writes"] == 0
    _forge_bundle(bundle,"services.csv",lambda rows: rows[0].update(title="OTHER SERVICE"))
    structural=verify_synthetic_cutover_export(bundle,structure_only=True)
    assert structural["ok"] and structural["manifestDigestVerified"] is False
    assert structural["mode"]=="SYNTHETIC_CUTOVER_EXPORT_STRUCTURE_ONLY"
    assert structural["manifestSha256"] != trusted
    with pytest.raises(SyntheticCutoverDenied,match="INVALID_EXPORT"):
        verify_synthetic_cutover_export(bundle,expected_manifest_sha256=trusted)


@pytest.mark.parametrize("digest",[None,"","a"*63,"A"*64,True])
def test_trusted_mode_rejects_missing_or_malformed_digest(tmp_path,digest):
    bundle,_=_bundle(tmp_path)
    with pytest.raises(SyntheticCutoverDenied,match="MANIFEST_DIGEST_REQUIRED"):
        verify_synthetic_cutover_export(bundle,expected_manifest_sha256=digest)


def test_conflicting_modes_are_rejected(tmp_path):
    bundle,trusted=_bundle(tmp_path)
    with pytest.raises(SyntheticCutoverDenied,match="MANIFEST_DIGEST_REQUIRED"):
        verify_synthetic_cutover_export(bundle,expected_manifest_sha256=trusted,
                                        structure_only=True)


@pytest.mark.parametrize("attack",["duplicate_root","duplicate_nested","bool_version",
                                   "float_version","nan","utf16","deep"])
def test_strict_manifest_even_with_matching_digest(tmp_path,attack):
    bundle,_=_bundle(tmp_path)
    p=bundle/"manifest.json"
    raw=p.read_bytes()
    if attack=="duplicate_root":
        raw=raw.replace(b'"version":1',b'"version":2,"version":1')
    elif attack=="duplicate_nested":
        raw=raw.replace(b'"vehicles":1',b'"vehicles":2,"vehicles":1')
    elif attack=="bool_version":
        raw=raw.replace(b'"version":1',b'"version":true')
    elif attack=="float_version":
        raw=raw.replace(b'"version":1',b'"version":1.0')
    elif attack=="nan":
        raw=raw.replace(b'"version":1',b'"version":NaN')
    elif attack=="utf16":
        raw=raw.decode("utf-8").encode("utf-16")
    elif attack=="deep":
        raw=b"["*1500+b"]"*1500
    p.write_bytes(raw)
    with pytest.raises(SyntheticCutoverDenied,match="INVALID_EXPORT"):
        verify_synthetic_cutover_export(bundle,expected_manifest_sha256=sha256(raw).hexdigest())


@pytest.mark.parametrize("attack",["extra","public_file","public_directory","symlink","fifo"])
def test_private_regular_files_only(tmp_path,attack):
    bundle,trusted=_bundle(tmp_path)
    path=bundle/"services.csv"
    if attack=="extra":
        (bundle/"extra").touch()
    elif attack=="public_file":
        path.chmod(0o644)
    elif attack=="public_directory":
        bundle.chmod(0o755)
    elif attack=="symlink":
        moved=tmp_path/"other.csv"
        path.rename(moved)
        path.symlink_to(moved)
    elif attack=="fifo":
        path.unlink()
        os.mkfifo(path,0o600)
    with pytest.raises(SyntheticCutoverDenied,match="INVALID_EXPORT"):
        verify_synthetic_cutover_export(bundle,expected_manifest_sha256=trusted)


def test_directory_replacement_cannot_redirect_reads(tmp_path,monkeypatch):
    bundle,trusted=_bundle(tmp_path)
    original=os.open
    def replace_after_manifest(path, flags, *args, **kwargs):
        fd=original(path,flags,*args,**kwargs)
        if path=="manifest.json" and kwargs.get("dir_fd") is not None:
            bundle.rename(tmp_path/"old")
            bundle.mkdir(mode=0o700)
        return fd
    monkeypatch.setattr(os,"open",replace_after_manifest)
    monkeypatch.setattr(os,"supports_dir_fd",os.supports_dir_fd | {replace_after_manifest})
    with pytest.raises(SyntheticCutoverDenied,match="INVALID_EXPORT"):
        verify_synthetic_cutover_export(bundle,expected_manifest_sha256=trusted)


def test_cli_requires_explicit_mode_and_never_prints_csv(tmp_path):
    bundle,trusted=_bundle(tmp_path)
    command=[sys.executable,"scripts/auto_cutover_export_verify.py","--synthetic-only"]
    env={**os.environ,"PYTHONPATH":os.pathsep.join((str(Path.cwd()/"apps/api"),str(Path.cwd())))}
    for args in ([],["--expect-manifest-sha256","0"*64],
                 ["--structure-only","--expect-manifest-sha256",trusted]):
        result=subprocess.run([*command,*args,str(bundle)],env=env,
                              capture_output=True,text=True)
        assert result.returncode!=0 and not result.stdout
    result=subprocess.run([*command,"--structure-only",str(bundle)],env=env,
                          capture_output=True,text=True,check=True)
    assert json.loads(result.stdout)["manifestDigestVerified"] is False
    assert "SYN-CAR-1" not in result.stdout and "FILTER-001" not in result.stdout
