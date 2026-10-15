"""Private, bounded FileStorage adapter for synthetic dev MinIO.

Not an HTTP route: domain ACL must be checked before every adapter call.
"""
from dataclasses import dataclass
import hashlib
from io import BytesIO
import os
from typing import Protocol
from urllib.parse import urlsplit
from uuid import uuid4

from minio import Minio
from minio.commonconfig import CopySource

DOCUMENTS = "roboport-documents-dev"
QUARANTINE = "roboport-quarantine-dev"
MAX_SIZE = 8 * 1024 * 1024
MIME = frozenset(("application/pdf", "image/png", "image/jpeg", "text/plain"))


@dataclass(frozen=True)
class StoredObject:
    key: str
    sha256: str
    size: int
    mime: str


class FileStorage(Protocol):
    def put(self, payload: bytes, mime: str) -> StoredObject: ...
    def get(self, key: str) -> bytes: ...
    def delete(self, key: str) -> None: ...
    def quarantine(self, key: str) -> None: ...


def _valid_key(key: str) -> bool:
    return (isinstance(key, str) and len(key) == 32
            and all(c in "0123456789abcdef" for c in key))


class MinioFileStorage:
    """No root/admin credentials and no public URLs or signed links."""

    def __init__(self, client: Minio):
        self._client = client

    @classmethod
    def from_environment(cls) -> "MinioFileStorage":
        endpoint = os.getenv("ROBOport_MINIO_ENDPOINT", "")
        url = urlsplit(endpoint)
        if (url.scheme != "http" or url.hostname != "127.0.0.1"
                or url.port != 9000 or url.path not in ("", "/")
                or url.username is not None or url.password is not None):
            raise ValueError("storage must use the isolated loopback endpoint")
        key = os.getenv("MINIO_APP_ACCESS_KEY", "")
        secret = os.getenv("MINIO_APP_SECRET_KEY", "")
        if not key or not secret or key == os.getenv("MINIO_ROOT_USER", "\0"):
            raise ValueError("app-only storage credentials required")
        return cls(Minio("127.0.0.1:9000", access_key=key, secret_key=secret, secure=False))

    def put(self, payload: bytes, mime: str) -> StoredObject:
        if not isinstance(payload, bytes) or not 0 < len(payload) <= MAX_SIZE:
            raise ValueError("invalid file size")
        if mime not in MIME:
            raise ValueError("unsupported mime")
        key = uuid4().hex  # random, contains no PII/device serial
        self._client.put_object(DOCUMENTS,key,BytesIO(payload),len(payload),content_type=mime)
        return StoredObject(key,hashlib.sha256(payload).hexdigest(),len(payload),mime)

    def get(self, key: str) -> bytes:
        if not _valid_key(key):
            raise ValueError("invalid object key")
        response = self._client.get_object(DOCUMENTS,key)
        try:
            content = response.read(MAX_SIZE + 1)
            if len(content) > MAX_SIZE:
                raise ValueError("oversized object")
            return content
        finally:
            response.close()
            response.release_conn()

    def delete(self, key: str) -> None:
        if not _valid_key(key):
            raise ValueError("invalid object key")
        self._client.remove_object(DOCUMENTS,key)

    def quarantine(self, key: str) -> None:
        if not _valid_key(key):
            raise ValueError("invalid object key")
        self._client.copy_object(QUARANTINE,key,CopySource(DOCUMENTS,key))
        self._client.remove_object(DOCUMENTS,key)
