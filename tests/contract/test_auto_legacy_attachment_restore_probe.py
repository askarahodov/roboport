"""Fail-closed contract for CI-only legacy attachment restore evidence (#165)."""
import json
from uuid import uuid4

import pytest

from scripts.auto_legacy_attachment_restore_probe import (
    FIELDS, digest, load_manifest, main,
)


def test_metadata_digest_is_deterministic_and_detects_tamper():
    original = {"attachments": [["a", "READY", "CLEAR"]],
                "replays": [["owner", "key"]]}
    assert digest(original) == digest(dict(reversed(list(original.items()))))
    assert digest(original) != digest({
        "attachments": [["a", "FAILED", "CLEAR"]],
        "replays": [["owner", "key"]],
    })


def test_manifest_rejects_missing_malformed_and_extra_fields(tmp_path):
    good = {"version": 1,
            "ids": {field: str(uuid4()) for field in FIELDS},
            "sha256": "a" * 64}
    file = tmp_path / "manifest.json"
    file.write_text(json.dumps(good), encoding="utf-8")
    assert load_manifest(file) == good
    for bad in (
        {**good, "version": 2},
        {**good, "extra": "untrusted"},
        {**good, "sha256": "invalid"},
        {**good, "ids": {**good["ids"], "ticket": "not-a-uuid"}},
        {**good, "ids": {k: v for k, v in good["ids"].items() if k != "ready"}},
    ):
        file.write_text(json.dumps(bad), encoding="utf-8")
        with pytest.raises((ValueError, TypeError)):
            load_manifest(file)


def test_cli_blocks_nonexistent_manifest_and_has_no_unsafe_db_target(tmp_path):
    missing = tmp_path / "missing.json"
    assert main(["verify-source", "--manifest", str(missing)]) == 1
    assert main(["verify-clone", "--manifest", str(missing)]) == 1
    with pytest.raises(SystemExit):
        main(["verify-clone", "--manifest", str(missing),
              "--database", "production"])
