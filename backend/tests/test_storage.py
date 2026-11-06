"""Storage behaviour.

Tests run against a local S3 server by default. Setting DOCUFLOW_TEST_S3_ENDPOINT runs the same
suite against a real backend, which is the conformance check required by ADR 0006.
"""

from __future__ import annotations

import hashlib
import io
import os
import uuid
from collections.abc import Iterator

import pytest

from docuflow.platform.crypto import DecryptionError, LocalKeyProvider
from docuflow.platform.storage import EncryptedObjects, ObjectNotFound, S3ObjectStore

MB = 1024 * 1024


@pytest.fixture(scope="module")
def endpoint() -> Iterator[str]:
    configured = os.environ.get("DOCUFLOW_TEST_S3_ENDPOINT")
    if configured:
        yield configured
        return
    from moto.server import ThreadedMotoServer

    server = ThreadedMotoServer(ip_address="127.0.0.1", port=0, verbose=False)
    server.start()
    _, port = server.get_host_and_port()
    yield f"http://127.0.0.1:{port}"
    server.stop()


@pytest.fixture(scope="module")
def store(endpoint: str) -> S3ObjectStore:
    instance = S3ObjectStore(
        endpoint,
        "us-east-1",
        f"docuflow-test-{uuid.uuid4().hex[:8]}",
        os.environ.get("DOCUFLOW_TEST_S3_ACCESS_KEY", "testing"),
        os.environ.get("DOCUFLOW_TEST_S3_SECRET_KEY", "testing"),
    )
    instance.ensure_bucket()
    return instance


@pytest.fixture
def objects(store: S3ObjectStore) -> EncryptedObjects:
    return EncryptedObjects(store, LocalKeyProvider(os.urandom(32)))


def never_stored(_sha: str) -> bool:
    return False


def test_plain_round_trip_and_size(store: S3ObjectStore) -> None:
    store.put("plain/one", io.BytesIO(b"hello"), 5)
    assert store.exists("plain/one")
    assert store.size("plain/one") == 5
    assert b"".join(store.get("plain/one")) == b"hello"


def test_range_read(store: S3ObjectStore) -> None:
    store.put("plain/range", io.BytesIO(b"0123456789"), 10)
    assert store.get_range("plain/range", 2, 5) == b"2345"


def test_multipart_upload_of_large_object(store: S3ObjectStore) -> None:
    body = os.urandom(20 * MB)
    store.put("plain/large", io.BytesIO(body), len(body))
    assert store.size("plain/large") == len(body)
    assert store.get_range("plain/large", 15 * MB, 15 * MB + 9) == body[15 * MB : 15 * MB + 10]


def test_missing_object_raises(store: S3ObjectStore) -> None:
    assert not store.exists("plain/none")
    with pytest.raises(ObjectNotFound):
        store.get("plain/none")
    with pytest.raises(ObjectNotFound):
        store.size("plain/none")


def test_delete(store: S3ObjectStore) -> None:
    store.put("plain/delete", io.BytesIO(b"x"), 1)
    store.delete("plain/delete")
    assert not store.exists("plain/delete")


def test_bucket_check_passes(store: S3ObjectStore) -> None:
    store.check()


def test_encrypted_round_trip_records_hash(objects: EncryptedObjects) -> None:
    tenant = uuid.uuid4()
    result = objects.put(tenant, [b"invoice ", b"content"], never_stored)
    assert result.key is not None
    assert result.wrapped_data_key is not None
    assert result.size == len(b"invoice content")
    assert result.sha256 == hashlib.sha256(b"invoice content").hexdigest()
    assert b"".join(objects.read(tenant, result.key, result.wrapped_data_key)) == b"invoice content"


def test_stored_bytes_are_encrypted(objects: EncryptedObjects, store: S3ObjectStore) -> None:
    result = objects.put(uuid.uuid4(), [b"CONFIDENTIAL"], never_stored)
    assert result.key is not None
    assert b"CONFIDENTIAL" not in b"".join(store.get(result.key))


def test_known_content_is_not_uploaded_again(objects: EncryptedObjects) -> None:
    result = objects.put(uuid.uuid4(), [b"duplicate"], lambda _sha: True)
    assert result.deduplicated
    assert result.key is None


def test_retry_after_crash_never_overwrites(objects: EncryptedObjects) -> None:
    """The first upload's database row was lost. The retry must not replace its object."""
    tenant = uuid.uuid4()
    first = objects.put(tenant, [b"same bytes"], never_stored)
    second = objects.put(tenant, [b"same bytes"], never_stored)
    assert first.sha256 == second.sha256
    assert first.key != second.key
    assert first.key is not None
    assert first.wrapped_data_key is not None
    assert b"".join(objects.read(tenant, first.key, first.wrapped_data_key)) == b"same bytes"


def test_encrypted_range_read_across_chunks(objects: EncryptedObjects) -> None:
    tenant = uuid.uuid4()
    body = os.urandom(3 * MB + 17)
    result = objects.put(tenant, [body], never_stored)
    assert result.key is not None
    assert result.wrapped_data_key is not None
    start, end = MB - 5, 2 * MB + 5
    data = objects.read_range(tenant, result.key, result.wrapped_data_key, start, end, len(body))
    assert data == body[start : end + 1]
    tail = objects.read_range(
        tenant, result.key, result.wrapped_data_key, len(body) - 3, len(body) - 1, len(body)
    )
    assert tail == body[-3:]


def test_range_outside_object_is_rejected(objects: EncryptedObjects) -> None:
    tenant = uuid.uuid4()
    result = objects.put(tenant, [b"short"], never_stored)
    assert result.key is not None
    assert result.wrapped_data_key is not None
    with pytest.raises(ValueError, match="outside"):
        objects.read_range(tenant, result.key, result.wrapped_data_key, 0, 100, 5)


def test_other_tenant_cannot_decrypt(objects: EncryptedObjects) -> None:
    result = objects.put(uuid.uuid4(), [b"private"], never_stored)
    assert result.key is not None
    assert result.wrapped_data_key is not None
    with pytest.raises(DecryptionError):
        b"".join(objects.read(uuid.uuid4(), result.key, result.wrapped_data_key))
