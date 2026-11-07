"""Malware scanning before a file is stored.

A scanner sees plaintext, so it runs before encryption. It fails closed: when it cannot give an
answer, the upload is refused rather than released unscanned.
"""

from __future__ import annotations

import socket
import struct
from dataclasses import dataclass
from typing import Any, BinaryIO, Protocol

from docuflow.platform.errors import DomainError

CHUNK = 64 * 1024


@dataclass(frozen=True)
class Verdict:
    clean: bool
    signature: str | None = None


class ScannerUnavailable(DomainError):
    status = 503
    code = "scanner_unavailable"


class Scanner(Protocol):
    name: str

    def scan(self, stream: BinaryIO) -> Verdict:
        """Reads the stream from its current position to the end."""
        ...


class NoScanner:
    """For development and tests. Production refuses to start with it (see Settings)."""

    name = "none"

    def scan(self, stream: BinaryIO) -> Verdict:
        return Verdict(clean=True)


class ClamdScanner:
    """Speaks the clamd INSTREAM protocol over TCP, so no client library is needed.

    clamd refuses a stream larger than its StreamMaxLength, which must be at least the upload
    limit. That refusal arrives as an error and is treated as unavailable, never as clean.
    """

    name = "clamav"

    def __init__(self, host: str, port: int, timeout_seconds: float = 60.0) -> None:
        self._address = (host, port)
        self._timeout = timeout_seconds

    def scan(self, stream: BinaryIO) -> Verdict:
        try:
            with socket.create_connection(self._address, timeout=self._timeout) as connection:
                connection.sendall(b"zINSTREAM\0")
                while chunk := stream.read(CHUNK):
                    connection.sendall(struct.pack("!I", len(chunk)) + chunk)
                connection.sendall(struct.pack("!I", 0))
                reply = self._read_reply(connection)
        except OSError as exc:
            raise ScannerUnavailable(
                "Files cannot be checked for viruses right now. Please try again later."
            ) from exc
        return self._interpret(reply)

    @staticmethod
    def _read_reply(connection: socket.socket) -> str:
        data = b""
        while not data.endswith(b"\0") and not data.endswith(b"\n"):
            part = connection.recv(4096)
            if not part:
                break
            data += part
        return data.decode("utf-8", errors="replace").strip("\0\n ")

    @staticmethod
    def _interpret(reply: str) -> Verdict:
        # Replies look like "stream: OK" or "stream: Eicar-Test-Signature FOUND".
        body = reply.split(":", 1)[-1].strip()
        if body == "OK":
            return Verdict(clean=True)
        if body.endswith(" FOUND"):
            return Verdict(clean=False, signature=body[: -len(" FOUND")].strip())
        raise ScannerUnavailable(
            "Files cannot be checked for viruses right now. Please try again later."
        )


def build_scanner(settings: Any) -> Scanner:
    """The scanner the settings ask for. Settings without a choice mean no scanner."""
    if getattr(settings, "scanner", "none") == "clamav":
        return ClamdScanner(
            settings.clamav_host, settings.clamav_port, settings.clamav_timeout_seconds
        )
    return NoScanner()
