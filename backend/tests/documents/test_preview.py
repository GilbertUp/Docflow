"""F05 over HTTP: pages are shown as images, and the file itself is never sent."""

from __future__ import annotations

import io
import uuid
from typing import Any

import pytest
from fastapi import FastAPI
from PIL import Image

from docuflow.platform.audit import AuditLog
from docuflow.platform.crypto import LocalKeyProvider
from docuflow.platform.db import Database
from tests.documents.files import mark_page, pdf_of_images, png_of, text_pdf
from tests.documents.test_upload import make, put
from tests.identity.support import audit_events, new_user
from tests.repository.support import allow, boss_of
from tests.web_support import API, MemoryObjectStore, make_app, sign_in

pytestmark = pytest.mark.db

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


@pytest.fixture
def app(db: Database, audit: AuditLog, keys: LocalKeyProvider) -> FastAPI:
    return make_app(db, audit, keys, MemoryObjectStore(), max_upload_bytes=2 * 1024 * 1024)


@pytest.fixture
def folder(app: FastAPI, db: Database, tenant: uuid.UUID) -> dict[str, Any]:
    boss_of(db, tenant)
    client, csrf = sign_in(app, db, tenant, "boss")
    cabinet = make(client, csrf, "/cabinets", "Finance")
    made = make(client, csrf, f"/cabinets/{cabinet['id']}/folders", "Scans")
    return {"client": client, "csrf": csrf, "folder": made}


def upload(ctx: dict[str, Any], name: str, body: bytes) -> dict[str, Any]:
    response = put(ctx["client"], ctx["csrf"], ctx["folder"]["id"], name, body)
    assert response.status_code == 201, response.text
    return response.json()["document"]


def test_a_pdf_is_shown_as_page_images_not_as_the_file(
    folder: dict[str, Any], db: Database, tenant: uuid.UUID
) -> None:
    client = folder["client"]
    document = upload(folder, "invoice.pdf", text_pdf(["Page one text", "Page two text"]))
    info = client.get(f"{API}/documents/{document['id']}/preview")
    assert info.status_code == 200, info.text
    body = info.json()
    assert (body["kind"], body["version"], len(body["pages"])) == ("pages", 1, 2)

    page = client.get(f"{API}/documents/{document['id']}/preview/pages/2", params={"width": 400})
    assert page.status_code == 200
    assert page.headers["content-type"] == "image/png"
    assert page.headers["cache-control"] == "private, no-store"
    assert page.content.startswith(PNG_MAGIC)
    assert not page.content.startswith(b"%PDF")
    assert Image.open(io.BytesIO(page.content)).width == pytest.approx(400, abs=2)

    (event,) = audit_events(db, tenant, "document.previewed")
    assert event.details["page"] == 2


def test_thumbnails_are_not_audited(
    folder: dict[str, Any], db: Database, tenant: uuid.UUID
) -> None:
    document = upload(folder, "invoice.pdf", text_pdf(["One"]))
    thumb = folder["client"].get(
        f"{API}/documents/{document['id']}/preview/pages/1", params={"width": 200}
    )
    assert thumb.status_code == 200
    assert audit_events(db, tenant, "document.previewed") == []


def test_an_image_and_a_multi_page_tiff_are_shown(folder: dict[str, Any]) -> None:
    client = folder["client"]
    photo = upload(folder, "photo.png", png_of(mark_page()))
    info = client.get(f"{API}/documents/{photo['id']}/preview").json()
    assert (info["kind"], len(info["pages"])) == ("pages", 1)
    shown = client.get(f"{API}/documents/{photo['id']}/preview/pages/1")
    assert shown.content.startswith(PNG_MAGIC)
    scan = upload(folder, "scan.pdf", pdf_of_images([mark_page(), mark_page()]))
    assert len(client.get(f"{API}/documents/{scan['id']}/preview").json()["pages"]) == 2


def test_a_missing_page_and_other_kinds_of_file_are_refused(folder: dict[str, Any]) -> None:
    client = folder["client"]
    document = upload(folder, "invoice.pdf", text_pdf(["One"]))
    assert client.get(f"{API}/documents/{document['id']}/preview/pages/9").status_code == 404
    assert client.get(f"{API}/documents/{document['id']}/preview/pages/0").status_code == 404
    assert (
        client.get(
            f"{API}/documents/{document['id']}/preview/pages/1", params={"width": 5}
        ).status_code
        == 422
    )
    note = upload(folder, "note.txt", b"Plain words")
    assert client.get(f"{API}/documents/{note['id']}/preview").json()["kind"] == "text"
    refused = client.get(f"{API}/documents/{note['id']}/preview/pages/1")
    assert refused.status_code == 415
    assert refused.json()["error"]["code"] == "preview_unavailable"


def test_seeing_a_page_needs_the_preview_right_and_visibility(
    app: FastAPI, folder: dict[str, Any], db: Database, tenant: uuid.UUID
) -> None:
    document = upload(folder, "invoice.pdf", text_pdf(["Secret"]))
    anchor = uuid.UUID(folder["folder"]["acl_anchor_id"])
    clerk_id = new_user(db, tenant, "clerk")
    clerk, _ = sign_in(app, db, tenant, "clerk")
    url = f"{API}/documents/{document['id']}/preview"
    assert clerk.get(url).status_code == 404  # cannot even see that it exists
    allow(db, tenant, anchor, clerk_id, "read")
    denied = clerk.get(url)
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "forbidden"
    allow(db, tenant, anchor, clerk_id, "preview")
    assert clerk.get(url).status_code == 200
    assert clerk.get(f"{url}/pages/1").status_code == 200
