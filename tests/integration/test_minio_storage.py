"""Real disposable MinIO smoke-test: run only from minio-dev workflow."""
import os
import hashlib

import pytest
from minio.error import S3Error
from roboport_api.storage import MinioFileStorage, MAX_SIZE, DOCUMENTS


@pytest.mark.skipif(os.getenv("ROBOport_MINIO_ENDPOINT") != "http://127.0.0.1:9000",
                    reason="needs isolated real Docker MinIO")
def test_real_storage_adapter_upload_read_quarantine_and_delete():
    storage=MinioFileStorage.from_environment()
    payload=b"synthetic-file-1"
    first=storage.put(payload,"text/plain")
    assert first.key and len(first.key)==32
    assert first.sha256==hashlib.sha256(payload).hexdigest()
    assert storage.get(first.key)==payload
    with pytest.raises(ValueError):
        storage.put(b"", "application/pdf")
    with pytest.raises(ValueError):
        storage.put(b"x"*(MAX_SIZE+1), "application/pdf")
    with pytest.raises(ValueError):
        storage.put(b"payload","text/html")
    with pytest.raises(ValueError):
        storage.get("../other-object")

    second=storage.put(b"synthetic-file-2","application/pdf")
    assert first.key!=second.key
    storage.quarantine(first.key)
    # Storage API cannot read quarantine; documents lookup is denied.
    with pytest.raises(S3Error):
        storage.get(first.key)
    assert storage.get(second.key)==b"synthetic-file-2"
    storage.delete(second.key)
    with pytest.raises(S3Error):
        storage.get(second.key)
