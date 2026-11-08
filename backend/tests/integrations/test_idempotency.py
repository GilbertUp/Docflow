"""Idempotency keys: a request sent twice does its work once."""

from __future__ import annotations

import hashlib
import uuid
from datetime import timedelta
from typing import Any

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy import select

from docuflow.modules.integrations.idempotency import MAX_BODY, IdempotencyMiddleware
from docuflow.modules.integrations.models import IdempotencyRecord
from docuflow.platform.audit import AuditLog
from docuflow.platform.crypto import LocalKeyProvider
from docuflow.platform.db import Database, utcnow
from tests.documents.files import text_pdf
from tests.documents.test_upload import make, put
from tests.identity.support import new_user
from tests.repository.support import allow, boss_of
from tests.web_support import API, MemoryObjectStore, make_app, sign_in

pytestmark = pytest.mark.db

AUTH = {"Authorization": "Bearer caller-one"}


def mini(db: Database, clock: Any = utcnow) -> tuple[TestClient, list[bytes]]:
    """A small app that counts how often its handlers really run."""
    app = FastAPI()
    seen: list[bytes] = []

    @app.post("/api/v1/things", status_code=201)
    async def things(request: Request) -> JSONResponse:
        seen.append(await request.body())
        return JSONResponse({"made": len(seen)}, status_code=201)

    @app.post("/api/v1/refuses")
    async def refuses(request: Request) -> JSONResponse:
        seen.append(await request.body())
        return JSONResponse({"error": "no"}, status_code=422)

    @app.post("/api/v1/explodes")
    async def explodes(request: Request) -> JSONResponse:
        seen.append(await request.body())
        raise RuntimeError("boom")

    @app.post("/api/v1/upload")
    async def upload(request: Request) -> JSONResponse:
        seen.append(await request.body())
        return JSONResponse({"made": len(seen)}, status_code=201)

    @app.post("/api/v1/webhooks")
    async def secrets_inside(request: Request) -> JSONResponse:
        seen.append(await request.body())
        return JSONResponse({"secret": "whsec_shown_once", "made": len(seen)}, status_code=201)

    @app.post("/api/v1/auth/login")
    async def login(request: Request) -> JSONResponse:
        seen.append(await request.body())
        return JSONResponse({"csrf_token": "x", "made": len(seen)}, status_code=200)

    @app.get("/api/v1/things")
    async def read() -> JSONResponse:
        seen.append(b"read")
        return JSONResponse({"read": len(seen)})

    app.add_middleware(IdempotencyMiddleware, db=db, clock=clock)
    return TestClient(app, raise_server_exceptions=False), seen


def key() -> dict[str, str]:
    return {"Idempotency-Key": uuid.uuid4().hex}


def test_the_same_key_and_request_does_the_work_once_and_returns_the_same_answer(
    db: Database,
) -> None:
    client, seen = mini(db)
    headers = {**AUTH, **key()}
    first = client.post("/api/v1/things", json={"a": 1}, headers=headers)
    second = client.post("/api/v1/things", json={"a": 1}, headers=headers)
    assert (first.status_code, first.json()) == (201, {"made": 1})
    assert (second.status_code, second.json()) == (201, {"made": 1})
    assert "idempotent-replay" not in first.headers
    assert second.headers["idempotent-replay"] == "true"
    assert second.headers["content-type"].startswith("application/json")
    assert len(seen) == 1


def test_a_different_key_or_no_key_is_a_new_request(db: Database) -> None:
    client, seen = mini(db)
    client.post("/api/v1/things", json={"a": 1}, headers={**AUTH, **key()})
    client.post("/api/v1/things", json={"a": 1}, headers={**AUTH, **key()})
    client.post("/api/v1/things", json={"a": 1}, headers=AUTH)
    client.post("/api/v1/things", json={"a": 1}, headers=AUTH)
    assert len(seen) == 4


def test_a_key_reused_for_a_different_request_is_refused(db: Database) -> None:
    client, seen = mini(db)
    headers = {**AUTH, **key()}
    client.post("/api/v1/things", json={"a": 1}, headers=headers)
    other_body = client.post("/api/v1/things", json={"a": 2}, headers=headers)
    assert other_body.status_code == 422
    assert other_body.json()["error"]["code"] == "idempotency_key_reused"
    other_path = client.post("/api/v1/refuses", json={"a": 1}, headers=headers)
    assert other_path.json()["error"]["code"] == "idempotency_key_reused"
    other_query = client.post("/api/v1/things?x=1", json={"a": 1}, headers=headers)
    assert other_query.json()["error"]["code"] == "idempotency_key_reused"
    assert len(seen) == 1


def test_two_callers_can_use_the_same_key_without_meeting(db: Database) -> None:
    client, seen = mini(db)
    shared = key()
    one = client.post(
        "/api/v1/things", json={"a": 1}, headers={"Authorization": "Bearer one", **shared}
    )
    two = client.post(
        "/api/v1/things", json={"a": 1}, headers={"Authorization": "Bearer two", **shared}
    )
    assert (one.json(), two.json()) == ({"made": 1}, {"made": 2})
    assert "idempotent-replay" not in two.headers  # it did not get the other caller's answer
    assert len(seen) == 2


def test_a_session_is_a_caller_too(db: Database) -> None:
    client, seen = mini(db)
    headers = {"Cookie": "docuflow_session=abc", **key()}
    client.post("/api/v1/things", json={"a": 1}, headers=headers)
    again = client.post("/api/v1/things", json={"a": 1}, headers=headers)
    assert again.headers["idempotent-replay"] == "true"
    assert len(seen) == 1


def test_a_failure_is_forgotten_so_a_corrected_request_can_use_the_same_key(db: Database) -> None:
    client, seen = mini(db)
    headers = {**AUTH, **key()}
    assert client.post("/api/v1/refuses", json={"a": 1}, headers=headers).status_code == 422
    # The same key can now be used for a corrected request, because nothing was kept.
    retry = client.post("/api/v1/refuses", json={"a": 2}, headers=headers)
    assert retry.status_code == 422
    assert retry.json() == {"error": "no"}  # a real answer, not the key-reuse error
    assert len(seen) == 2


def test_a_request_that_crashes_is_forgotten_too(db: Database) -> None:
    client, seen = mini(db)
    headers = {**AUTH, **key()}
    assert client.post("/api/v1/explodes", json={}, headers=headers).status_code == 500
    assert client.post("/api/v1/explodes", json={}, headers=headers).status_code == 500
    assert len(seen) == 2  # it ran again rather than replaying a failure
    with db.platform_session() as session:
        found = session.scalars(
            select(IdempotencyRecord).where(IdempotencyRecord.key == headers["Idempotency-Key"])
        ).all()
        assert found == []


def test_a_request_still_being_worked_on_is_not_started_again(db: Database) -> None:
    client, seen = mini(db)
    headers = {**AUTH, **key()}
    body = b'{"a": 1}'
    caller = hashlib.sha256(AUTH["Authorization"].encode()).hexdigest()
    request_hash = hashlib.sha256(b"\0".join([b"POST", b"/api/v1/things", b"", body])).hexdigest()
    with db.platform_session() as session:
        session.add(
            IdempotencyRecord(
                caller_hash=caller,
                key=headers["Idempotency-Key"],
                request_hash=request_hash,
                state="processing",
                created_at=utcnow(),
            )
        )
    busy = client.post(
        "/api/v1/things", content=body, headers={**headers, "Content-Type": "application/json"}
    )
    assert busy.status_code == 409
    assert busy.json()["error"]["code"] == "idempotency_in_progress"
    assert seen == []


def test_a_file_upload_with_a_key_is_not_covered_and_says_so(db: Database) -> None:
    client, seen = mini(db)
    headers = {**AUTH, **key(), "Content-Type": "application/octet-stream"}
    first = client.post("/api/v1/upload", content=b"%PDF bytes", headers=headers)
    second = client.post("/api/v1/upload", content=b"%PDF bytes", headers=headers)
    assert first.headers["idempotency-ignored"] == "only JSON bodies are covered"
    assert (first.json(), second.json()) == ({"made": 1}, {"made": 2})
    assert len(seen) == 2


def test_answers_that_hold_secrets_are_never_kept(db: Database) -> None:
    client, seen = mini(db)
    headers = {**AUTH, **key()}
    client.post("/api/v1/webhooks", json={"a": 1}, headers=headers)
    again = client.post("/api/v1/webhooks", json={"a": 1}, headers=headers)
    assert "idempotent-replay" not in again.headers
    assert again.json()["made"] == 2
    login = {**AUTH, **key()}
    client.post("/api/v1/auth/login", json={"a": 1}, headers=login)
    client.post("/api/v1/auth/login", json={"a": 1}, headers=login)
    assert len(seen) == 4
    with db.platform_session() as session:
        stored = session.scalars(
            select(IdempotencyRecord).where(
                IdempotencyRecord.key.in_([headers["Idempotency-Key"], login["Idempotency-Key"]])
            )
        ).all()
        assert stored == []


def test_a_bad_key_a_huge_body_and_no_credential_are_handled(db: Database) -> None:
    client, seen = mini(db)
    bad = client.post("/api/v1/things", json={}, headers={**AUTH, "Idempotency-Key": "has spaces!"})
    assert bad.status_code == 400
    assert bad.json()["error"]["code"] == "invalid_idempotency_key"
    too_long = client.post(
        "/api/v1/things", json={}, headers={**AUTH, "Idempotency-Key": "k" * 256}
    )
    assert too_long.status_code == 400
    huge = client.post(
        "/api/v1/things",
        content=b"{" + b" " * MAX_BODY + b"}",
        headers={**AUTH, **key(), "Content-Type": "application/json"},
    )
    assert huge.status_code == 413
    anonymous = client.post("/api/v1/things", json={}, headers=key())
    assert anonymous.status_code == 201  # passed on, and refused for real by the app's own checks
    assert len(seen) == 1


def test_reads_and_unkeyed_writes_pass_straight_through(db: Database) -> None:
    client, seen = mini(db)
    assert client.get("/api/v1/things", headers={**AUTH, **key()}).json() == {"read": 1}
    assert client.get("/api/v1/things", headers={**AUTH, **key()}).json() == {"read": 2}
    assert len(seen) == 2


def test_old_records_are_forgotten(db: Database) -> None:
    client, _seen = mini(db)
    stale = uuid.uuid4().hex
    with db.platform_session() as session:
        session.add(
            IdempotencyRecord(
                caller_hash="0" * 64,
                key=stale,
                request_hash="1" * 64,
                state="complete",
                status_code=201,
                body=b"{}",
                created_at=utcnow() - timedelta(days=2),
            )
        )
    client.post("/api/v1/things", json={"a": 1}, headers={**AUTH, **key()})
    with db.platform_session() as session:
        assert (
            session.scalars(select(IdempotencyRecord).where(IdempotencyRecord.key == stale)).all()
            == []
        )


def test_the_app_cannot_read_the_stored_answers(db: Database, engines: dict[str, Any]) -> None:
    from sqlalchemy import text
    from sqlalchemy.exc import ProgrammingError

    with engines["app"].connect() as connection:
        with pytest.raises(ProgrammingError):
            connection.execute(text("SELECT count(*) FROM idempotency_record"))


# Through the real application


@pytest.fixture
def app(db: Database, audit: AuditLog, keys: LocalKeyProvider) -> FastAPI:
    return make_app(db, audit, keys, MemoryObjectStore(), max_upload_bytes=1024 * 1024)


def test_a_comment_sent_twice_with_the_same_key_is_made_once(
    app: FastAPI, db: Database, tenant: uuid.UUID
) -> None:
    boss_of(db, tenant)
    client, csrf = sign_in(app, db, tenant, "boss")
    cabinet = make(client, csrf, "/cabinets", "Finance")
    folder = make(client, csrf, f"/cabinets/{cabinet['id']}/folders", "Invoices")
    doc = put(client, csrf, folder["id"], "a.pdf", text_pdf(["A document"])).json()["document"]
    url = f"{API}/documents/{doc['id']}/comments"
    headers = {**csrf, "Idempotency-Key": uuid.uuid4().hex}

    first = client.post(url, json={"body": "Once"}, headers=headers)
    second = client.post(url, json={"body": "Once"}, headers=headers)
    assert (first.status_code, second.status_code) == (201, 201)
    assert second.json() == first.json()
    assert second.headers["idempotent-replay"] == "true"
    assert len(client.get(url).json()) == 1

    third = client.post(url, json={"body": "Twice"}, headers=headers)
    assert third.status_code == 422
    assert third.json()["error"]["code"] == "idempotency_key_reused"
    assert len(client.get(url).json()) == 1

    # Another person with the same key is a different caller.
    other_id = new_user(db, tenant, "other")
    allow(db, tenant, uuid.UUID(folder["acl_anchor_id"]), other_id, "read", "annotate")
    other, other_csrf = sign_in(app, db, tenant, "other")
    mine = other.post(
        url,
        json={"body": "Mine"},
        headers={**other_csrf, "Idempotency-Key": headers["Idempotency-Key"]},
    )
    assert mine.status_code == 201
    assert len(client.get(url).json()) == 2


def test_a_client_that_retries_the_instant_it_hears_back_gets_the_stored_answer(
    db: Database,
) -> None:
    """The answer must be kept before the last bytes go out, not after, or a fast retry would be
    told the first request was still being worked on."""
    import asyncio

    async def app(scope: Any, receive: Any, send: Any) -> None:
        await receive()
        headers = [(b"content-type", b"application/json")]
        await send({"type": "http.response.start", "status": 201, "headers": headers})
        await send({"type": "http.response.body", "body": b'{"made":1}'})

    middleware = IdempotencyMiddleware(app, db)
    body = b'{"a": 1}'
    key = uuid.uuid4().hex
    caller = hashlib.sha256(b"Bearer fast").hexdigest()
    request_hash = hashlib.sha256(b"\0".join([b"POST", b"/api/v1/things", b"", body])).hexdigest()
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/v1/things",
        "query_string": b"",
        "headers": [
            (b"idempotency-key", key.encode()),
            (b"authorization", b"Bearer fast"),
            (b"content-type", b"application/json"),
        ],
    }
    states: list[str] = []

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        if message["type"] == "http.response.body":
            # What a client's retry would meet at the moment it has the whole answer.
            states.append(middleware._begin(caller, key, request_hash)[0])

    asyncio.run(middleware(scope, receive, send))
    assert states == ["replay"]
