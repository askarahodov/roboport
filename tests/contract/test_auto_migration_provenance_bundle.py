"""Contract tests: 4-domain provenance inventory never grants actual migration."""
import hashlib
import json
from uuid import uuid4

from scripts.auto_migration_provenance_bundle import (
    KINDS, inspect_provenance_bundle,
)


def make_bundle(tmp_path, *, missing=(), altered=False):
    entries = []
    for kind in KINDS:
        if kind in missing:
            continue
        name = kind + ".FIXTURE"
        data = ("SYNTHETIC " + kind + "\n").encode()
        (tmp_path / name).write_bytes(data)
        entries.append({
            "kind": kind, "file": name, "bytes": len(data),
            "records": 1, "sha256": hashlib.sha256(data).hexdigest(),
            "source_system": "DEMO", "legal_basis_ref": "FAKE-LEGAL-REF",
            "owner_proof_ref": "FAKE-OWNERSHIP-REF",
        })
    manifest = {
        "schema": 1, "synthetic_only": True,
        "organization_id": str(uuid4()), "location_id": str(uuid4()),
        "sources": entries,
    }
    if altered:
        manifest["sources"][0]["sha256"] = "0" * 64
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path, manifest


def test_all_four_domains_have_verified_hashes_but_no_production_permission(tmp_path):
    path, manifest = make_bundle(tmp_path)
    result = inspect_provenance_bundle(path)
    assert result == {
        "ok": True, "counts": {kind: 1 for kind in KINDS},
        "mode": "PROVENANCE_PREFLIGHT_ONLY", "writes": 0,
        "approval": "NOT_VERIFIED", "release_allowed": False,
    }
    assert inspect_provenance_bundle(path) == result
    assert manifest["organization_id"] not in str(result)
    assert "FAKE-OWNERSHIP-REF" not in str(result)


def test_missing_domain_and_digest_mismatch_fail_closed(tmp_path):
    path, _ = make_bundle(tmp_path, missing=("DOCUMENT",))
    assert inspect_provenance_bundle(path)["code"] == "SCOPE_INCOMPLETE"
    path, _ = make_bundle(tmp_path, altered=True)
    assert inspect_provenance_bundle(path)["code"] == "SOURCE_MISMATCH"


def test_symlink_and_unapproved_real_data_fail_closed(tmp_path):
    path, manifest = make_bundle(tmp_path)
    source = tmp_path / manifest["sources"][0]["file"]
    target = tmp_path / "backup.FIXTURE"
    source.rename(target)
    source.symlink_to(target)
    assert inspect_provenance_bundle(path)["code"] == "UNAVAILABLE"
    source.unlink()
    source.write_bytes(target.read_bytes())
    manifest["synthetic_only"] = False
    path.write_text(json.dumps(manifest))
    assert inspect_provenance_bundle(path)["code"] == "APPROVAL_REQUIRED"


def test_unsafe_paths_and_unchecked_evidence_cannot_be_used(tmp_path):
    path, manifest = make_bundle(tmp_path)
    manifest["sources"][0]["file"] = "../secrets"
    path.write_text(json.dumps(manifest))
    assert inspect_provenance_bundle(path)["code"] == "SOURCE_NAME"
    manifest["sources"][0]["file"] = "CUSTOMER.FIXTURE"
    manifest["sources"][0]["owner_proof_ref"] = ""
    path.write_text(json.dumps(manifest))
    assert inspect_provenance_bundle(path)["code"] == "EVIDENCE_REQUIRED"


def test_extra_sensitive_columns_rejected(tmp_path):
    path, manifest = make_bundle(tmp_path)
    manifest["sources"][0]["customer_name"] = "someone"
    path.write_text(json.dumps(manifest))
    assert inspect_provenance_bundle(path)["code"] == "SCHEMA"


def test_manifest_duplicate_keys_are_rejected_fail_closed(tmp_path):
    path, _ = make_bundle(tmp_path)
    original = path.read_text(encoding="utf-8")
    path.write_text(original.replace('"synthetic_only": true', '"synthetic_only": false, "synthetic_only": true'), encoding="utf-8")
    assert inspect_provenance_bundle(path)["code"] == "SCHEMA"


def test_nested_duplicate_keys_are_rejected_fail_closed(tmp_path):
    path, _ = make_bundle(tmp_path)
    original = path.read_text(encoding="utf-8")
    path.write_text(original.replace('"kind": "CUSTOMER"', '"kind": "DOCUMENT", "kind": "CUSTOMER"'), encoding="utf-8")
    assert inspect_provenance_bundle(path)["code"] == "SCHEMA"


def test_boolean_schema_version_is_not_integer_one(tmp_path):
    path, manifest = make_bundle(tmp_path)
    manifest["schema"] = True
    path.write_text(json.dumps(manifest), encoding="utf-8")
    assert inspect_provenance_bundle(path)["code"] == "APPROVAL_REQUIRED"
