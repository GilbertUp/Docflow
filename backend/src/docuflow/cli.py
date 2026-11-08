"""Operator commands and long-running service loops."""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import uuid
from datetime import UTC, datetime

import structlog
from sqlalchemy import select

from docuflow.platform.bootstrap import Services, get_services
from docuflow.platform.jobs import Dispatcher, Maintenance, enqueue
from docuflow.platform.models import Tenant
from docuflow.platform.observability import configure_logging

log = structlog.get_logger("docuflow.cli")
DISPATCH_POLL_SECONDS = 1.0
SCHEDULER_POLL_SECONDS = 15.0
STORAGE_WAIT_ATTEMPTS = 30


def create_tenant(services: Services, name: str) -> None:
    with services.db.platform_session() as session:
        tenant = Tenant(name=name, slug=re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-"))
        session.add(tenant)
        session.flush()
        services.audit.record(
            session,
            tenant.id,
            "tenant.created",
            object_type="tenant",
            object_id=str(tenant.id),
            details={"name": name},
        )
        print(tenant.id)


def ensure_bucket(services: Services) -> None:
    """Storage may start after migrations, so wait for it rather than failing the deploy."""
    for attempt in range(STORAGE_WAIT_ATTEMPTS):
        try:
            services.store.ensure_bucket()
        except Exception:
            log.warning("storage_not_ready", attempt=attempt + 1)
            time.sleep(2)
        else:
            return
    services.store.ensure_bucket()


def verify_audit(services: Services, tenant_id: uuid.UUID) -> int:
    with services.db.tenant_session(tenant_id) as session:
        chain = services.audit.verify(session, tenant_id)
        anchors = services.audit.verify_checkpoints(session, tenant_id, services.store)
    print(json.dumps({"chain": chain.__dict__, "checkpoints": anchors.__dict__}))
    return 0 if chain.ok and anchors.ok else 1


def run_dispatcher(services: Services) -> None:
    from docuflow.worker import publish  # keeps Celery out of other commands

    dispatcher = Dispatcher(services.db, publish)
    log.info("dispatcher_started")
    while True:
        try:
            if dispatcher.publish_batch() == 0:
                time.sleep(DISPATCH_POLL_SECONDS)
        except Exception:  # outages must not end the loop; the outbox retains work
            log.exception("dispatch_failed")
            time.sleep(5)


def schedule_checkpoints(services: Services) -> None:
    hour = datetime.now(UTC).strftime("%Y%m%d%H")
    with services.db.platform_session() as session:
        for tenant_id in session.scalars(select(Tenant.id).where(Tenant.status == "active")):
            enqueue(session, tenant_id, "audit.checkpoint", f"audit.checkpoint:{hour}")


def run_scheduler(services: Services) -> None:
    maintenance = Maintenance(
        services.db, services.audit, services.settings.job_heartbeat_timeout_seconds
    )
    log.info("scheduler_started")
    while True:
        try:
            result = maintenance.tick()
            schedule_checkpoints(services)
            if any(result.values()):
                log.info("maintenance_tick", **result)
        except Exception:  # a later tick retries; timers stay in PostgreSQL
            log.exception("maintenance_failed")
        time.sleep(SCHEDULER_POLL_SECONDS)


def ocr_check(services: Services, languages: list[str]) -> int:
    """Reads a drawn sample page, to show that the OCR engine and its language packs work."""
    from PIL import Image, ImageDraw, ImageFont

    from docuflow.modules.documents.ocr import build_ocr_engine

    engine = build_ocr_engine(services.settings)
    if not engine.available():
        print(json.dumps({"ok": False, "error": "the OCR engine is not installed"}))
        return 1
    missing = [code for code in languages if code not in engine.languages()]
    image = Image.new("RGB", (900, 300), "white")
    ImageDraw.Draw(image).multiline_text(
        (40, 40),
        "Invoice 1042" + chr(10) + "Supplier ABC",
        fill="black",
        font=ImageFont.load_default(size=44),
    )
    text = engine.recognise(image, [code for code in languages if code not in missing]).text
    ok = not missing and "Invoice" in text and "1042" in text
    print(
        json.dumps(
            {
                "ok": ok,
                "engine": engine.version(),
                "languages_installed": len(engine.languages()),
                "missing": missing,
                "sample": text.replace(chr(10), " / "),
            }
        )
    )
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="docuflow")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("ensure-bucket", help="Create the storage bucket if needed")
    commands.add_parser("dispatcher", help="Publish committed outbox rows")
    commands.add_parser("scheduler", help="Fire timers and recover stalled jobs")
    tenant = commands.add_parser("create-tenant", help="Create an organization")
    tenant.add_argument("name")
    verify = commands.add_parser("verify-audit", help="Verify a tenant's audit chain")
    verify.add_argument("tenant_id", type=uuid.UUID)
    check = commands.add_parser("ocr-check", help="Read a sample page to test the OCR engine")
    check.add_argument("languages", nargs="*", default=["eng"])
    args = parser.parse_args(argv)

    services = get_services()
    configure_logging(services.settings.log_level)
    match args.command:
        case "ensure-bucket":
            ensure_bucket(services)
        case "dispatcher":
            run_dispatcher(services)
        case "scheduler":
            run_scheduler(services)
        case "create-tenant":
            create_tenant(services, args.name)
        case "verify-audit":
            return verify_audit(services, args.tenant_id)
        case "ocr-check":
            return ocr_check(services, args.languages)
    return 0


if __name__ == "__main__":
    sys.exit(main())
