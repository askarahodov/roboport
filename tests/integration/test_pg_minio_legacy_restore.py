"""Real disposable PG17 + MinIO restore drill for legacy attachments (#165).

Run only in minio-dev CI. Two private source buckets are reconciled against
a scoped SQL graph, archived to a temporary file, and restored to distinct
private buckets. No production operations or source-object deletion.
"""
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import subprocess
from zipfile import ZIP_STORED, ZipFile

import psycopg
import pytest
from minio import Minio
from minio.error import S3Error

from roboport_api.storage import DOCUMENTS, QUARANTINE, MAX_SIZE, MinioFileStorage
from scripts.auto_legacy_attachment_restore_probe import digest, graph, seed_graph
from scripts.auto_restore_lifecycle_probe import _dsn

PAYLOADS = {"ready": b"ready", "failed": b"failed"}
RESTORE_DB = "roboport_restored"
pytestmark = pytest.mark.skipif(
    os.getenv("ROBOport_MINIO_ENDPOINT") != "http://127.0.0.1:9000",
    reason="requires isolated MinIO workflow",
)


def read_object(client, bucket, key):
    result = client.get_object(bucket, key)
    try:
        payload = result.read(MAX_SIZE + 1)
        if len(payload) > MAX_SIZE:
            raise ValueError("oversized object")
        return payload
    finally:
        result.close()
        result.release_conn()


def put_object(client, bucket, key, payload, mime="text/plain"):
    client.put_object(bucket, key, BytesIO(payload), len(payload), content_type=mime)


def assert_absent(client, bucket, key):
    try:
        client.stat_object(bucket, key)
    except S3Error as exc:
        if exc.code not in {"NoSuchKey", "NoSuchObject", "NotFound"}:
            raise
        return
    raise ValueError("object in incorrect bucket")


def records(snapshot, ids):
    by_id = {row[0]: row for row in snapshot["attachments"]}
    return {
        role: {
            "key": by_id[ids[role]][2],
            "mime": by_id[ids[role]][3],
            "size": int(by_id[ids[role]][4]),
            "sha256": by_id[ids[role]][5],
            "bucket": DOCUMENTS if role == "ready" else QUARANTINE,
        }
        for role in PAYLOADS
    }


def reconcile(client, expected, buckets):
    """Return verified bytes, fail on missing, duplicated or corrupted objects."""
    result = {}
    for role, item in expected.items():
        bucket = buckets[item["bucket"]]
        other = buckets[QUARANTINE if item["bucket"] == DOCUMENTS else DOCUMENTS]
        assert_absent(client, other, item["key"])
        stat = client.stat_object(bucket, item["key"])
        payload = read_object(client, bucket, item["key"])
        if (stat.size != item["size"] or len(payload) != item["size"]
                or stat.content_type != item["mime"]
                or hashlib.sha256(payload).hexdigest() != item["sha256"]):
            raise ValueError("attachment object reconciliation mismatch")
        result[role] = payload
    return result


def backup(path, expected, payloads, graph_sha):
    with ZipFile(path, "x", compression=ZIP_STORED) as archive:
        archive.writestr("manifest.json", json.dumps(
            {"version": 1, "graphSha256": graph_sha, "objects": expected},
            sort_keys=True, separators=(",", ":"),
        ))
        for role, payload in payloads.items():
            archive.writestr(role + ".bin", payload)


def restore(path, expected, graph_sha, client, buckets):
    """Validate complete archive before writing only to empty restore buckets."""
    with ZipFile(path) as archive:
        if sorted(archive.namelist()) != ["failed.bin", "manifest.json", "ready.bin"]:
            raise ValueError("unexpected archive members")
        if any(info.file_size > MAX_SIZE for info in archive.infolist()):
            raise ValueError("oversized archive member")
        manifest = json.loads(archive.read("manifest.json"))
        if manifest != {"version": 1, "graphSha256": graph_sha, "objects": expected}:
            raise ValueError("archive manifest mismatch")
        payloads = {role: archive.read(role + ".bin") for role in expected}
        for role, item in expected.items():
            payload = payloads[role]
            if (len(payload) != item["size"]
                    or hashlib.sha256(payload).hexdigest() != item["sha256"]):
                raise ValueError("archive payload mismatch")
    if len(set(buckets.values())) != 2 or set(buckets.values()) & {DOCUMENTS, QUARANTINE}:
        raise ValueError("restore destination must be isolated")
    for bucket in buckets.values():
        if list(client.list_objects(bucket, recursive=True)):
            raise ValueError("restore destination is not empty")
    for role, item in expected.items():
        put_object(client, buckets[item["bucket"]], item["key"], payloads[role], item["mime"])


def pg_restore(tmp_path):
    """Use the real CI postgres:17 container; never restore into the source."""
    container = os.environ["ROBOport_RESTORE_PG_CONTAINER"]
    if not container or not all(c in "0123456789abcdef" for c in container):
        raise ValueError("invalid disposable container id")
    def run(*args, **kwargs):
        return subprocess.run(["docker", "exec", "-i", container, *args],
                              check=True, timeout=240, **kwargs)
    dump = tmp_path / "legacy-attachments.dump"
    with dump.open("wb") as out:
        run("pg_dump", "-U", "roboport", "-Fc", "roboport", stdout=out)
    run("createdb", "-U", "roboport", RESTORE_DB)
    with dump.open("rb") as source:
        run("pg_restore", "-U", "roboport", "--exit-on-error", "-d", RESTORE_DB,
            stdin=source)


def test_legacy_attachment_physical_backup_restore_and_corruption_detection(tmp_path):
    # Explicit opt-in plus fixed disposable DSN (including ambient PG checks).
    assert os.getenv("ROBOport_LEGACY_RESTORE_CI") == "1"
    source_dsn, clone_dsn = _dsn("roboport"), _dsn(RESTORE_DB)
    storage = MinioFileStorage.from_environment()
    source = storage._client
    target = Minio("127.0.0.1:9000",
                   access_key=os.environ["MINIO_APP_ACCESS_KEY"],
                   secret_key=os.environ["MINIO_APP_SECRET_KEY"], secure=False)
    buckets = {DOCUMENTS: "roboport-documents-restore-dev",
               QUARANTINE: "roboport-quarantine-restore-dev"}
    original_buckets = {DOCUMENTS: DOCUMENTS, QUARANTINE: QUARANTINE}

    with psycopg.connect(source_dsn) as db:
        ids = seed_graph(db)
        snapshot = graph(db, ids)
    expected = records(snapshot, ids)
    graph_sha = digest(snapshot)
    for role, item in expected.items():
        # Only new synthetic keys, never existing objects.
        for bucket in original_buckets.values():
            assert_absent(source, bucket, item["key"])
        put_object(source, item["bucket"], item["key"], PAYLOADS[role], item["mime"])
    assert reconcile(source, expected, original_buckets) == PAYLOADS
    # FileStorage's ordinary document lookup must not reach quarantine.
    assert storage.get(expected["ready"]["key"]) == PAYLOADS["ready"]
    with pytest.raises(S3Error):
        storage.get(expected["failed"]["key"])

    archive = tmp_path / "legacy-attachments.zip"
    backup(archive, expected, PAYLOADS, graph_sha)
    pg_restore(tmp_path)
    with psycopg.connect(clone_dsn) as db:
        db.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        restored = graph(db, ids)
        assert digest(restored) == graph_sha
        restored_expected = records(restored, ids)
    assert restored_expected == expected

    # Reject a tampered archive without a single destination write.
    damaged = tmp_path / "damaged.zip"
    backup(damaged, expected, {**PAYLOADS, "ready": b"WRONG"}, graph_sha)
    with pytest.raises(ValueError, match="archive payload mismatch"):
        restore(damaged, restored_expected, graph_sha, target, buckets)
    assert all(not list(target.list_objects(bucket, recursive=True))
               for bucket in buckets.values())
    restore(archive, restored_expected, graph_sha, target, buckets)
    assert reconcile(target, restored_expected, buckets) == PAYLOADS
    with pytest.raises(ValueError, match="not empty"):
        restore(archive, restored_expected, graph_sha, target, buckets)

    # Negative probes are confined to the restored disposable buckets.
    ready = expected["ready"]
    failed = expected["failed"]
    target.remove_object(buckets[DOCUMENTS], ready["key"])
    with pytest.raises(S3Error):
        reconcile(target, restored_expected, buckets)
    put_object(target, buckets[DOCUMENTS], ready["key"], b"WRONG")
    with pytest.raises(ValueError, match="reconciliation mismatch"):
        reconcile(target, restored_expected, buckets)
    put_object(target, buckets[DOCUMENTS], ready["key"], PAYLOADS["ready"])
    put_object(target, buckets[DOCUMENTS], failed["key"], PAYLOADS["failed"])
    with pytest.raises(ValueError, match="incorrect bucket"):
        reconcile(target, restored_expected, buckets)
    target.remove_object(buckets[DOCUMENTS], failed["key"])
    assert reconcile(target, restored_expected, buckets) == PAYLOADS
    assert reconcile(source, expected, original_buckets) == PAYLOADS
    with psycopg.connect(source_dsn) as db:
        db.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        assert digest(graph(db, ids)) == graph_sha
