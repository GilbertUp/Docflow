"""ADR 0006: envelope encryption, tamper detection and range reads."""

from __future__ import annotations

import os
import uuid

import pytest

from docuflow.platform import crypto
from docuflow.platform.crypto import DecryptionError, LocalKeyProvider

CHUNK = 1024
KEY = b"k" * 32
AAD = b"object"


def seal(plaintext: bytes, chunk: int = CHUNK) -> bytes:
    return b"".join(crypto.encrypt_stream([plaintext], KEY, AAD, chunk))


def unseal(ciphertext: bytes, chunk: int = CHUNK, key: bytes = KEY, aad: bytes = AAD) -> bytes:
    return b"".join(crypto.decrypt_stream([ciphertext], key, aad, chunk))


@pytest.mark.parametrize("size", [0, 1, CHUNK - 1, CHUNK, CHUNK + 1, CHUNK * 3, CHUNK * 3 + 7])
def test_round_trip_at_chunk_boundaries(size: int) -> None:
    plaintext = os.urandom(size)
    sealed = seal(plaintext)
    assert unseal(sealed) == plaintext
    assert len(sealed) == crypto.ciphertext_size(size, CHUNK)


def test_round_trip_with_uneven_input_pieces() -> None:
    plaintext = os.urandom(CHUNK * 2 + 5)
    pieces = [plaintext[i : i + 333] for i in range(0, len(plaintext), 333)]
    sealed = b"".join(crypto.encrypt_stream(pieces, KEY, AAD, CHUNK))
    reads = [sealed[i : i + 700] for i in range(0, len(sealed), 700)]
    assert b"".join(crypto.decrypt_stream(reads, KEY, AAD, CHUNK)) == plaintext


def test_flipped_bit_is_detected() -> None:
    sealed = bytearray(seal(b"a" * 100))
    sealed[-20] ^= 1
    with pytest.raises(DecryptionError):
        unseal(bytes(sealed))


def test_truncation_at_chunk_boundary_is_detected() -> None:
    sealed = seal(os.urandom(CHUNK * 3))
    keep = crypto.HEADER_SIZE + 2 * (CHUNK + crypto.TAG_SIZE)
    with pytest.raises(DecryptionError):
        unseal(sealed[:keep])


def test_swapped_chunks_are_detected() -> None:
    sealed = seal(os.urandom(CHUNK * 3))
    size = CHUNK + crypto.TAG_SIZE
    body = sealed[crypto.HEADER_SIZE :]
    swapped = sealed[: crypto.HEADER_SIZE] + body[size : 2 * size] + body[:size] + body[2 * size :]
    with pytest.raises(DecryptionError):
        unseal(swapped)


def test_ciphertext_cannot_be_moved_to_another_object() -> None:
    with pytest.raises(DecryptionError):
        unseal(seal(b"secret"), aad=b"other-object")


def test_wrong_key_fails() -> None:
    with pytest.raises(DecryptionError):
        unseal(seal(b"secret"), key=b"x" * 32)


def test_plaintext_is_not_visible_in_ciphertext() -> None:
    assert b"CONFIDENTIAL" not in seal(b"CONFIDENTIAL" * 100)


def test_identical_content_encrypts_differently() -> None:
    assert seal(b"same") != seal(b"same")


@pytest.mark.parametrize(
    ("start", "end"),
    [(0, 0), (0, 99), (CHUNK - 1, CHUNK), (CHUNK, CHUNK * 2 - 1), (10, CHUNK * 3 + 6)],
)
def test_range_reads(start: int, end: int) -> None:
    plaintext = os.urandom(CHUNK * 3 + 7)
    sealed = seal(plaintext)
    first, _, cipher_start, cipher_end = crypto.chunk_span(start, end, CHUNK)
    result = crypto.decrypt_range(
        sealed[: crypto.HEADER_SIZE],
        sealed[cipher_start : cipher_end + 1],
        first,
        start,
        end,
        len(plaintext),
        KEY,
        AAD,
        CHUNK,
    )
    assert result == plaintext[start : end + 1]


def test_range_cannot_be_read_from_the_wrong_position() -> None:
    plaintext = os.urandom(CHUNK * 3)
    sealed = seal(plaintext)
    _, _, cipher_start, cipher_end = crypto.chunk_span(0, 0, CHUNK)
    with pytest.raises(DecryptionError):
        crypto.decrypt_range(
            sealed[: crypto.HEADER_SIZE],
            sealed[cipher_start : cipher_end + 1],
            2,
            CHUNK * 2,
            CHUNK * 2,
            len(plaintext),
            KEY,
            AAD,
            CHUNK,
        )


def test_wrapped_key_is_bound_to_its_tenant() -> None:
    provider = LocalKeyProvider(os.urandom(32))
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    wrapped = provider.wrap(tenant_a, KEY)
    assert provider.unwrap(tenant_a, wrapped) == KEY
    with pytest.raises(DecryptionError):
        provider.unwrap(tenant_b, wrapped)


def test_object_key_hides_content_hash_and_tenant() -> None:
    provider = LocalKeyProvider(os.urandom(32))
    tenant = uuid.uuid4()
    sha = "a" * 64
    key = crypto.object_key(provider.object_name_key(tenant), tenant, sha, "unique")
    assert sha not in key
    assert str(tenant) not in key
    assert tenant.hex not in key
