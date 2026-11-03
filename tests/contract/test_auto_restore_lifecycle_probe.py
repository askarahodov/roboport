"""Read-only contract guard for the CI-only automotive pg_dump/pg_restore probe."""
import json
from pathlib import Path
from uuid import uuid4

import pytest

from scripts.auto_restore_lifecycle_probe import (
    FIELDS, _digest, _dsn, _load, main,
)


def fixture_manifest():
    return {
        "version": 1,
        "ids": {field: str(uuid4()) for field in FIELDS},
        "sha256": "a"*64,
    }


def test_restore_digest_is_deterministic_and_detects_any_live_graph_mutation():
    original = {"appointment": [["a", "CREATED"]],
                "stock": [["b", "RESERVE", "3"]]}
    reordered = {"stock": [["b", "RESERVE", "3"]],
                 "appointment": [["a", "CREATED"]]}
    changed = {"stock": [["b", "RESERVE", "2"]],
               "appointment": [["a", "CREATED"]]}
    assert _digest(original) == _digest(reordered)
    assert _digest(original) != _digest(changed)
    assert len(_digest(original)) == 64


def test_manifest_requires_exact_uuid_fields_and_rejects_tampered_metadata(tmp_path):
    good=fixture_manifest()
    file=tmp_path/"proof.json"
    file.write_text(json.dumps(good),encoding="utf-8")
    assert _load(file)==good
    cases=[
        {**good,"version":2},
        {**good,"extra":"unexpected"},
        {**good,"sha256":"x"},
        {**good,"ids":{**good["ids"],"other":"x"}},
        {**good,"ids":{k:v for k,v in good["ids"].items() if k!="partSku"}},
        {**good,"ids":{**good["ids"],"appointment":"not-a-uuid"}},
        {**good,"ids":{**good["ids"],"workOrder":123}},
    ]
    for invalid in cases:
        file.write_text(json.dumps(invalid),encoding="utf-8")
        with pytest.raises((ValueError,TypeError)):
            _load(file)


def test_restore_connections_are_strictly_disposable_loopback(monkeypatch):
    monkeypatch.setenv(
        "ROBOport_DATABASE_URL",
        "postgresql+psycopg://roboport:roboport@127.0.0.1:5432/roboport",
    )
    assert _dsn("roboport").endswith(":5432/roboport")
    assert _dsn("roboport_restored").endswith(":5432/roboport_restored")
    with pytest.raises(ValueError):
        _dsn("production")
    for source in (
        "postgresql://roboport:roboport@db.example.org:5432/roboport",
        "postgresql://roboport:roboport@127.0.0.1:5432/production",
        "postgresql://roboport:roboport@127.0.0.1:5433/roboport",
        "mysql://roboport:roboport@127.0.0.1:5432/roboport",
    ):
        monkeypatch.setenv("ROBOport_DATABASE_URL",source)
        with pytest.raises(ValueError):
            _dsn("roboport")


def test_verification_has_no_unsafe_mutating_source_mode(tmp_path):
    manifest=tmp_path/"missing.json"
    with pytest.raises(SystemExit):
        main(["verify-clone","--manifest",str(manifest),"--database","roboport"])
    with pytest.raises(SystemExit):
        main(["seed","--manifest",str(manifest),"--database","roboport_restored"])
    assert main(["verify","--manifest",str(manifest),"--database","roboport"]) == 1
