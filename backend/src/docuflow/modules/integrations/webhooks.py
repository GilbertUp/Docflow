"""The rules for sending webhooks that do not depend on the database (F20).

- Only a short list of events can be subscribed to, and a payload says what happened and which
  object it is about, never what was in it. A receiver fetches the rest through the API with its
  own key, so a webhook can never carry more than the receiver's key would be allowed to see.
- An address must be safe to call from inside the network: https, no embedded password, and every
  address it resolves to must be a public one. A webhook cannot be pointed at the database, the
  cloud's metadata service or anything else on the private network.
- Every call is signed with the subscription's secret over its timestamp and body, so a receiver
  can tell it came from here, was not changed, and is not an old one being sent again.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import socket
import time
from collections.abc import Callable
from typing import Any, Protocol
from urllib.parse import urlsplit

import httpx

MAX_URL = 500
TIMEOUT_SECONDS = 10.0
TOLERANCE_SECONDS = 300
SIGNATURE_HEADER = "X-DocuFlow-Signature"
EVENT_HEADER = "X-DocuFlow-Event"
DELIVERY_HEADER = "X-DocuFlow-Delivery"

# The events that can be subscribed to. Reads, sign-ins and the webhooks' own events are not here,
# so a webhook cannot report on itself or on who looked at what.
EVENTS: dict[str, str] = {
    "document.uploaded": "A document is filed",
    "document.version_added": "A new version is added",
    "document.version_restored": "An earlier version is made current again",
    "document.indexed": "A document's type or details are saved",
    "document.text_extracted": "A document's text has been read",
    "document.fields_suggested": "Values were suggested from a document's text",
    "document.commented": "Someone comments",
    "document.stamped": "A stamp is placed",
    "document.link_added": "Two documents are linked",
    "document.form_submitted": "A form is submitted on a document",
    "document.compressed": "A smaller copy was made or tried",
    "document.disposed": "A document was destroyed",
    "task.created": "A task is given to someone",
    "task.updated": "A task changes",
    "task.escalated": "A task is escalated",
    "workflow.started": "A workflow starts on a document",
    "workflow.step_decided": "A step is approved, rejected or completed",
    "workflow.completed": "A workflow finishes approved",
    "workflow.rejected": "A workflow ends rejected",
    "workflow.cancelled": "A workflow is stopped",
    "signature.created": "A document is signed",
    "signature.revoked": "A signature is revoked",
    "form.submitted": "A form is submitted",
}
PING = "webhook.ping"


class UnsafeUrl(ValueError):
    """The address cannot be used for a webhook. The message is for the person who typed it."""


Resolver = Callable[[str, int], list[str]]


def resolve(host: str, port: int) -> list[str]:
    """The addresses a name points to."""
    try:
        found = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError:
        return []
    return sorted({str(item[4][0]) for item in found})


def _public(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


def check_url(url: str, *, allow_insecure: bool = False, resolver: Resolver = resolve) -> None:
    """Raises UnsafeUrl unless the address is safe to call. Called when it is saved and again
    just before every delivery, because names can change what they point to."""
    if not url or len(url) > MAX_URL:
        raise UnsafeUrl(f"The address must be 1 to {MAX_URL} characters.")
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError as exc:
        raise UnsafeUrl("That is not a valid address.") from exc
    allowed = ("https", "http") if allow_insecure else ("https",)
    if parts.scheme not in allowed:
        raise UnsafeUrl("The address must start with https://.")
    if not parts.hostname:
        raise UnsafeUrl("The address needs a host name.")
    if parts.username or parts.password:
        raise UnsafeUrl("The address cannot contain a user name or password.")
    addresses = resolver(parts.hostname, port or (443 if parts.scheme == "https" else 80))
    if not addresses:
        raise UnsafeUrl("That host name cannot be found.")
    if not all(_public(a) for a in addresses):
        raise UnsafeUrl(
            "That address points to a private or internal network, which is not allowed."
        )


def sign(secret: str, timestamp: int, body: bytes) -> str:
    """The signature header for a call: the time and a keyed hash of the time and the body."""
    digest = hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={digest}"


def verify(
    secret: str,
    header: str,
    body: bytes,
    *,
    now: float | None = None,
    tolerance: int = TOLERANCE_SECONDS,
) -> bool:
    """What a receiver does: check the signature and that the call is recent."""
    try:
        fields = dict(item.split("=", 1) for item in header.split(","))
        timestamp = int(fields["t"])
        given = fields["v1"]
    except (KeyError, ValueError):
        return False
    if abs((now if now is not None else time.time()) - timestamp) > tolerance:
        return False
    expected = sign(secret, timestamp, body).split("v1=", 1)[1]
    return hmac.compare_digest(given, expected)


def encode(payload: dict[str, Any]) -> bytes:
    """The body as it is sent and signed: compact, with keys in a fixed order."""
    return json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()


class Sender(Protocol):
    def send(self, url: str, headers: dict[str, str], body: bytes) -> int: ...


class HttpSender:
    """Sends the call. It never follows a redirect, which could lead somewhere not checked."""

    def __init__(
        self, timeout: float = TIMEOUT_SECONDS, client: httpx.Client | None = None
    ) -> None:
        self._client = client or httpx.Client(timeout=timeout, follow_redirects=False)

    def send(self, url: str, headers: dict[str, str], body: bytes) -> int:
        return self._client.post(url, content=body, headers=headers).status_code
