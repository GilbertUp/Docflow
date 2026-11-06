"""Object storage (ADR 0006). Domain code sees bytes and hashes, never the S3 backend."""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
import uuid
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from typing import Any, BinaryIO, Protocol, cast

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from docuflow.platform import crypto
from docuflow.platform.crypto import KeyProvider
from docuflow.platform.ids import uuid7

READ_SIZE = 1024 * 1024
NOT_FOUND_CODES = {"404", "NoSuchKey", "NotFound"}


class ObjectNotFound(Exception):
    pass


class ObjectStore(Protocol):
    def put(self, key: str, body: BinaryIO, length: int) -> None: ...

    def exists(self, key: str) -> bool: ...

    def size(self, key: str) -> int: ...

    def get(self, key: str) -> Iterator[bytes]: ...

    def get_range(self, key: str, start: int, end: int) -> bytes: ...

    def delete(self, key: str) -> None: ...

    def check(self) -> None: ...


class S3ObjectStore:
    """Works with any S3-compatible backend."""

    def __init__(
        self, endpoint: str, region: str, bucket: str, access_key: str, secret_key: str
    ) -> None:
        self._bucket = bucket
        self._client: Any = boto3.client(
            "s3",
            endpoint_url=endpoint,
            region_name=region,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            config=Config(
                signature_version="s3v4",
                s3={"addressing_style": "path"},
                retries={"max_attempts": 5, "mode": "standard"},
                connect_timeout=5,
                read_timeout=60,
            ),
        )

    def ensure_bucket(self) -> None:
        try:
            self._client.head_bucket(Bucket=self._bucket)
        except ClientError as exc:
            if exc.response["Error"]["Code"] not in NOT_FOUND_CODES:
                raise
            self._client.create_bucket(Bucket=self._bucket)

    def put(self, key: str, body: BinaryIO, length: int) -> None:
        self._client.upload_fileobj(body, self._bucket, key)

    def exists(self, key: str) -> bool:
        try:
            self._client.head_object(Bucket=self._bucket, Key=key)
        except ClientError as exc:
            if exc.response["Error"]["Code"] in NOT_FOUND_CODES:
                return False
            raise
        return True

    def size(self, key: str) -> int:
        try:
            head = self._client.head_object(Bucket=self._bucket, Key=key)
        except ClientError as exc:
            if exc.response["Error"]["Code"] in NOT_FOUND_CODES:
                raise ObjectNotFound(key) from exc
            raise
        return int(head["ContentLength"])

    def get(self, key: str) -> Iterator[bytes]:
        try:
            response = self._client.get_object(Bucket=self._bucket, Key=key)
        except ClientError as exc:
            if exc.response["Error"]["Code"] in NOT_FOUND_CODES:
                raise ObjectNotFound(key) from exc
            raise
        return cast("Iterator[bytes]", response["Body"].iter_chunks(READ_SIZE))

    def get_range(self, key: str, start: int, end: int) -> bytes:
        try:
            response = self._client.get_object(
                Bucket=self._bucket, Key=key, Range=f"bytes={start}-{end}"
            )
        except ClientError as exc:
            if exc.response["Error"]["Code"] in NOT_FOUND_CODES:
                raise ObjectNotFound(key) from exc
            raise
        return cast("bytes", response["Body"].read())

    def delete(self, key: str) -> None:
        self._client.delete_object(Bucket=self._bucket, Key=key)

    def check(self) -> None:
        self._client.head_bucket(Bucket=self._bucket)


@dataclass(frozen=True)
class StoredObject:
    """Result of a store. key and wrapped_data_key are None when deduplicated."""

    sha256: str
    size: int
    key: str | None
    wrapped_data_key: bytes | None
    deduplicated: bool


class EncryptedObjects:
    """Stores immutable, encrypted objects.

    Content is encrypted to a temporary file while hashing, so plaintext never rests on disk.
    The caller's already_stored check consults the database, the only authority on what exists.
    Each object has a fresh data key and a unique storage key, and is never written twice.
    """

    def __init__(self, store: ObjectStore, keys: KeyProvider) -> None:
        self._store = store
        self._keys = keys

    def put(
        self,
        tenant_id: uuid.UUID,
        content: Iterable[bytes],
        already_stored: Callable[[str], bool],
    ) -> StoredObject:
        data_key = os.urandom(32)
        digest = hashlib.sha256()
        size = 0

        def tracked() -> Iterator[bytes]:
            nonlocal size
            for chunk in content:
                digest.update(chunk)
                size += len(chunk)
                yield chunk

        aad = tenant_id.bytes
        with tempfile.TemporaryFile() as spool:
            for sealed in crypto.encrypt_stream(tracked(), data_key, aad):
                spool.write(sealed)
            length = spool.tell()
            sha256 = digest.hexdigest()
            if already_stored(sha256):
                return StoredObject(sha256, size, None, None, deduplicated=True)
            key = crypto.object_key(
                self._keys.object_name_key(tenant_id), tenant_id, sha256, uuid7().hex
            )
            spool.seek(0)
            self._store.put(key, cast("BinaryIO", spool), length)
        return StoredObject(
            sha256, size, key, self._keys.wrap(tenant_id, data_key), deduplicated=False
        )

    def read(self, tenant_id: uuid.UUID, stored_key: str, wrapped_key: bytes) -> Iterator[bytes]:
        data_key = self._keys.unwrap(tenant_id, wrapped_key)
        return crypto.decrypt_stream(self._store.get(stored_key), data_key, tenant_id.bytes)

    def read_range(
        self,
        tenant_id: uuid.UUID,
        stored_key: str,
        wrapped_key: bytes,
        start: int,
        end: int,
        plaintext_size: int,
    ) -> bytes:
        if not 0 <= start <= end < plaintext_size:
            raise ValueError("range is outside the object")
        data_key = self._keys.unwrap(tenant_id, wrapped_key)
        first, _, cipher_start, cipher_end = crypto.chunk_span(start, end)
        last_byte = crypto.ciphertext_size(plaintext_size) - 1
        header = self._store.get_range(stored_key, 0, crypto.HEADER_SIZE - 1)
        sealed = self._store.get_range(stored_key, cipher_start, min(cipher_end, last_byte))
        return crypto.decrypt_range(
            header, sealed, first, start, end, plaintext_size, data_key, tenant_id.bytes
        )

    def delete(self, stored_key: str) -> None:
        self._store.delete(stored_key)


def copy_to_temp(stream: BinaryIO) -> BinaryIO:
    """Spool an upload to a private temporary file. Names never derive from user input."""
    target = tempfile.TemporaryFile()
    shutil.copyfileobj(stream, target, READ_SIZE)
    target.seek(0)
    return cast("BinaryIO", target)
