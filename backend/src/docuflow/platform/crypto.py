"""Envelope encryption with authenticated, seekable chunks (ADR 0006).

Each object has its own data key, wrapped by a tenant key. Plaintext is split into fixed chunks,
each sealed with AES-256-GCM. The nonce carries the chunk index and a final-chunk flag, and the
object identity is bound as associated data. Reordering, truncating or moving chunks between
objects therefore fails decryption, and a byte range can be read without the whole object.
"""

from __future__ import annotations

import hashlib
import hmac
import itertools
import os
import struct
import uuid
from collections.abc import Iterable, Iterator
from typing import Protocol

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

CHUNK_SIZE = 1024 * 1024
TAG_SIZE = 16
NONCE_PREFIX_SIZE = 7
MAGIC = b"DFE1"
HEADER_SIZE = len(MAGIC) + NONCE_PREFIX_SIZE


class DecryptionError(Exception):
    """Ciphertext was altered, truncated, or does not belong to this object."""


class KeyProvider(Protocol):
    """Implemented by the local provider here, and by Vault or KMS adapters in production."""

    def wrap(self, tenant_id: uuid.UUID, data_key: bytes) -> bytes: ...

    def unwrap(self, tenant_id: uuid.UUID, wrapped: bytes) -> bytes: ...

    def object_name_key(self, tenant_id: uuid.UUID) -> bytes: ...


class LocalKeyProvider:
    """Derives tenant keys from a root key supplied by secret management."""

    def __init__(self, root_key: bytes) -> None:
        self._root_key = root_key

    def _derive(self, tenant_id: uuid.UUID, purpose: bytes) -> bytes:
        return HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=tenant_id.bytes,
            info=b"docuflow:" + purpose,
        ).derive(self._root_key)

    def wrap(self, tenant_id: uuid.UUID, data_key: bytes) -> bytes:
        nonce = os.urandom(12)
        sealed = AESGCM(self._derive(tenant_id, b"kek")).encrypt(nonce, data_key, tenant_id.bytes)
        return nonce + sealed

    def unwrap(self, tenant_id: uuid.UUID, wrapped: bytes) -> bytes:
        try:
            return AESGCM(self._derive(tenant_id, b"kek")).decrypt(
                wrapped[:12], wrapped[12:], tenant_id.bytes
            )
        except InvalidTag as exc:
            raise DecryptionError("data key does not belong to this tenant") from exc

    def object_name_key(self, tenant_id: uuid.UUID) -> bytes:
        return self._derive(tenant_id, b"object-name")


def object_key(name_key: bytes, tenant_id: uuid.UUID, sha256_hex: str, unique_id: str) -> str:
    """Storage key that reveals neither tenant nor content hash, and is never reused.

    The unique part means a retry after a crash writes a new object instead of overwriting one
    whose wrapped key may be lost. The database decides deduplication, not the bucket.
    """
    digest = hmac.new(name_key, sha256_hex.encode(), hashlib.sha256).hexdigest()
    prefix = hashlib.sha256(tenant_id.bytes).hexdigest()[:16]
    return f"{prefix}/{digest[:2]}/{digest}-{unique_id}"


def _nonce(prefix: bytes, index: int, final: bool) -> bytes:
    return prefix + struct.pack(">I", index) + (b"\x01" if final else b"\x00")


def _rechunk(chunks: Iterable[bytes], size: int) -> Iterator[bytes]:
    buffer = bytearray()
    for chunk in chunks:
        buffer.extend(chunk)
        while len(buffer) >= size:
            yield bytes(buffer[:size])
            del buffer[:size]
    if buffer:
        yield bytes(buffer)


def encrypt_stream(
    plaintext: Iterable[bytes], data_key: bytes, aad: bytes, chunk_size: int = CHUNK_SIZE
) -> Iterator[bytes]:
    aead = AESGCM(data_key)
    prefix = os.urandom(NONCE_PREFIX_SIZE)
    yield MAGIC + prefix
    pending: bytes | None = None
    index = 0
    for chunk in _rechunk(plaintext, chunk_size):
        if pending is not None:
            yield aead.encrypt(_nonce(prefix, index, False), pending, aad)
            index += 1
        pending = chunk
    # Empty objects still get one sealed final chunk, so truncation is always detectable.
    yield aead.encrypt(_nonce(prefix, index, True), pending or b"", aad)


def decrypt_stream(
    ciphertext: Iterable[bytes], data_key: bytes, aad: bytes, chunk_size: int = CHUNK_SIZE
) -> Iterator[bytes]:
    aead = AESGCM(data_key)
    source = iter(ciphertext)
    buffered = bytearray()
    while len(buffered) < HEADER_SIZE:
        piece = next(source, None)
        if piece is None:
            break
        buffered.extend(piece)
    header = bytes(buffered[:HEADER_SIZE])
    if header[: len(MAGIC)] != MAGIC or len(header) != HEADER_SIZE:
        raise DecryptionError("not a DocuFlow encrypted object")
    prefix = header[len(MAGIC) :]
    remainder = bytes(buffered[HEADER_SIZE:])
    pending: bytes | None = None
    index = 0
    for sealed in _rechunk(itertools.chain([remainder], source), chunk_size + TAG_SIZE):
        if pending is not None:
            yield _open(aead, _nonce(prefix, index, False), pending, aad)
            index += 1
        pending = sealed
    if pending is None:
        raise DecryptionError("object is truncated")
    yield _open(aead, _nonce(prefix, index, True), pending, aad)


def _open(aead: AESGCM, nonce: bytes, sealed: bytes, aad: bytes) -> bytes:
    try:
        return aead.decrypt(nonce, sealed, aad)
    except InvalidTag as exc:
        raise DecryptionError("chunk failed authentication") from exc


def ciphertext_size(plaintext_size: int, chunk_size: int = CHUNK_SIZE) -> int:
    chunks = max(1, -(-plaintext_size // chunk_size))
    return HEADER_SIZE + plaintext_size + chunks * TAG_SIZE


def chunk_span(start: int, end: int, chunk_size: int = CHUNK_SIZE) -> tuple[int, int, int, int]:
    """Map an inclusive plaintext range to (first chunk, last chunk, cipher start, cipher end)."""
    first = start // chunk_size
    last = end // chunk_size
    sealed = chunk_size + TAG_SIZE
    return first, last, HEADER_SIZE + first * sealed, HEADER_SIZE + (last + 1) * sealed - 1


def decrypt_range(
    header: bytes,
    sealed_chunks: bytes,
    first_chunk: int,
    start: int,
    end: int,
    plaintext_size: int,
    data_key: bytes,
    aad: bytes,
    chunk_size: int = CHUNK_SIZE,
) -> bytes:
    """Decrypt only the chunks covering [start, end]; object size decides the final flag."""
    if header[: len(MAGIC)] != MAGIC or len(header) != HEADER_SIZE:
        raise DecryptionError("not a DocuFlow encrypted object")
    prefix = header[len(MAGIC) :]
    aead = AESGCM(data_key)
    final_index = max(0, (plaintext_size - 1) // chunk_size)
    sealed_size = chunk_size + TAG_SIZE
    plain = bytearray()
    for offset in range(0, len(sealed_chunks), sealed_size):
        index = first_chunk + offset // sealed_size
        nonce = _nonce(prefix, index, index == final_index)
        plain.extend(_open(aead, nonce, sealed_chunks[offset : offset + sealed_size], aad))
    begin = start - first_chunk * chunk_size
    return bytes(plain[begin : begin + (end - start + 1)])
