"""Making a write safe to send twice (F20).

A client that does not know whether its request arrived sends it again. With an
``Idempotency-Key`` header the second send gets the first answer back instead of doing the work
twice.

Rules that hold throughout:
- A key belongs to the caller who sent it. Two callers can use the same key without meeting, and
  one can never read the other's stored answer.
- The same key with a different request is refused, because that is a mistake the client needs to
  hear about, not a retry.
- A request still being worked on is not started again: the second send is told to wait.
- Only a successful answer is kept. Anything else is forgotten, so the client can fix the request
  and send it again with the same key.
- Only JSON bodies up to 1 MiB are covered, because the whole body must be compared. A file upload
  with a key is not covered, and the answer says so rather than seeming safe to repeat.
- Sign-in, API keys and webhook secrets are never kept: their answers hold secrets.
- Records are forgotten after a day.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from docuflow.modules.integrations.models import IdempotencyRecord
from docuflow.platform.db import Database, utcnow
from docuflow.platform.ids import uuid7

WRITES = frozenset({"POST", "PUT", "PATCH", "DELETE"})
MAX_BODY = 1024 * 1024
MAX_STORED = 256 * 1024
KEY = re.compile(r"^[A-Za-z0-9_.:-]{1,255}$")
KEPT_FOR = timedelta(days=1)
NEVER_KEPT = ("/auth/", "/api-keys", "/webhooks")


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


@dataclass
class _Captured:
    """What the application answered, as far as it will be kept."""

    status: int = 0
    content_type: str | None = None
    chunks: list[bytes] = field(default_factory=list)
    size: int = 0
    too_big: bool = False


class IdempotencyMiddleware:
    def __init__(self, app: ASGIApp, db: Database, clock: Callable[[], datetime] = utcnow) -> None:
        self.app = app
        self._db = db
        self._clock = clock

    # The store

    def _begin(
        self, caller: str, key: str, request_hash: str
    ) -> tuple[str, int | None, str | None, bytes | None]:
        """new, replay, mismatch or busy, with the stored answer for a replay."""
        now = self._clock()
        with self._db.platform_session() as session:
            session.execute(
                delete(IdempotencyRecord).where(IdempotencyRecord.created_at < now - KEPT_FOR)
            )
            inserted = session.execute(
                insert(IdempotencyRecord)
                .values(
                    id=uuid7(),
                    caller_hash=caller,
                    key=key,
                    request_hash=request_hash,
                    state="processing",
                    created_at=now,
                )
                .on_conflict_do_nothing(constraint="uq_idempotency_caller_key")
                .returning(IdempotencyRecord.id)
            ).first()
            if inserted is not None:
                return "new", None, None, None
            row = session.scalar(
                select(IdempotencyRecord).where(
                    IdempotencyRecord.caller_hash == caller, IdempotencyRecord.key == key
                )
            )
            if row is None:  # forgotten between the two statements: start again as new
                return "busy", None, None, None
            if row.request_hash != request_hash:
                return "mismatch", None, None, None
            if row.state != "complete":
                return "busy", None, None, None
            return "replay", row.status_code, row.content_type, row.body

    def _finish(
        self, caller: str, key: str, status: int, content_type: str | None, body: bytes
    ) -> None:
        with self._db.platform_session() as session:
            row = session.scalar(
                select(IdempotencyRecord).where(
                    IdempotencyRecord.caller_hash == caller, IdempotencyRecord.key == key
                )
            )
            if row is not None:
                row.state, row.status_code, row.content_type, row.body = (
                    "complete",
                    status,
                    content_type,
                    body,
                )

    def _forget(self, caller: str, key: str) -> None:
        with self._db.platform_session() as session:
            session.execute(
                delete(IdempotencyRecord).where(
                    IdempotencyRecord.caller_hash == caller, IdempotencyRecord.key == key
                )
            )

    # The request

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] not in WRITES:
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        key = headers.get("idempotency-key")
        path = scope["path"]
        if key is None or not path.startswith("/api/v1/") or any(p in path for p in NEVER_KEPT):
            await self.app(scope, receive, send)
            return
        credential = headers.get("authorization") or headers.get("cookie")
        if not credential:
            await self.app(scope, receive, send)  # not signed in, so it will be refused anyway
            return
        if not KEY.match(key):
            await _error(
                400,
                "invalid_idempotency_key",
                "The key must be 1 to 255 letters, digits or - _ . :",
            )(scope, receive, send)
            return

        body = b""
        more = True
        while more:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body += message.get("body", b"")
            more = message.get("more_body", False)
            if len(body) > MAX_BODY:
                await _error(
                    413, "payload_too_large", "With an Idempotency-Key the body can be 1 MiB."
                )(scope, receive, send)
                return

        content_type = headers.get("content-type", "").split(";")[0].strip().lower()
        replay_body = body

        async def replayed() -> Message:
            nonlocal replay_body
            chunk, replay_body = replay_body, b""
            return {"type": "http.request", "body": chunk, "more_body": False}

        if body and content_type != "application/json":

            async def mark(message: Message) -> None:
                if message["type"] == "http.response.start":
                    message.setdefault("headers", []).append(
                        (b"idempotency-ignored", b"only JSON bodies are covered")
                    )
                await send(message)

            await self.app(scope, replayed, mark)
            return

        caller = hashlib.sha256(credential.encode()).hexdigest()
        request_hash = hashlib.sha256(
            b"\0".join(
                [scope["method"].encode(), path.encode(), scope.get("query_string", b""), body]
            )
        ).hexdigest()
        state, status, stored_type, stored = await run_in_threadpool(
            self._begin, caller, key, request_hash
        )
        if state == "mismatch":
            await _error(
                422,
                "idempotency_key_reused",
                "That key was used for a different request. Use a new key for a new request.",
            )(scope, receive, send)
            return
        if state == "busy":
            await _error(
                409,
                "idempotency_in_progress",
                "That request is still being worked on. Try again shortly.",
            )(scope, receive, send)
            return
        if state == "replay":
            payload = stored or b""
            await send(
                {
                    "type": "http.response.start",
                    "status": status or 200,
                    "headers": [
                        (b"content-type", (stored_type or "application/json").encode()),
                        (b"content-length", str(len(payload)).encode()),
                        (b"idempotent-replay", b"true"),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": payload})
            return

        seen = _Captured()
        settled = False

        async def settle() -> None:
            """Keeps or forgets the answer. It must happen before the last bytes go out, or a
            client that retries the instant it hears back would find the request unfinished."""
            nonlocal settled
            if settled:
                return
            settled = True
            if 200 <= seen.status < 300 and not seen.too_big:
                await run_in_threadpool(
                    self._finish, caller, key, seen.status, seen.content_type, b"".join(seen.chunks)
                )
            else:
                await run_in_threadpool(self._forget, caller, key)

        async def capture(message: Message) -> None:
            if message["type"] == "http.response.start":
                seen.status = message["status"]
                seen.content_type = Headers(raw=message["headers"]).get("content-type")
            elif message["type"] == "http.response.body":
                chunk = message.get("body", b"")
                seen.size += len(chunk)
                if seen.size > MAX_STORED:
                    seen.too_big = True
                elif not seen.too_big:
                    seen.chunks.append(chunk)
                if not message.get("more_body", False):
                    await settle()
            await send(message)

        try:
            await self.app(scope, replayed, capture)
        except BaseException:
            if not settled:
                settled = True
                await run_in_threadpool(self._forget, caller, key)
            raise
        await settle()  # an answer with no body at all
