"""Local demo server: embedded PostgreSQL with in-memory stand-ins for Redis and S3.

FOR LOCAL DEMOS ONLY. It runs without Docker, binds to 127.0.0.1, uses fixed role passwords for
the throwaway database, and loses uploaded objects on exit. Production uses the Compose stack.

    python backend/scripts/dev_server.py --seed

Data lives in ./.data/dev by default (ignored by Git). With --seed it creates a demo
organization with Finance and HR cabinets, and writes the generated sign-in details to
credentials.txt in the data folder.
"""

from __future__ import annotations

import argparse
import json
import secrets
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import psycopg
import uvicorn
from alembic import command
from alembic.config import Config
from moto.server import ThreadedMotoServer
from sqlalchemy import make_url, select

from docuflow.main import create_app
from docuflow.modules.compression.service import JOB_TYPE as COMPRESS_JOB
from docuflow.modules.compression.service import CompressionService
from docuflow.modules.documents.extraction import JOB_TYPE as EXTRACT_JOB
from docuflow.modules.documents.imports import JOB_TYPE
from docuflow.modules.documents.wiring import build_documents
from docuflow.modules.identity import security
from docuflow.modules.identity.auth import Principal
from docuflow.modules.identity.catalogue import SYSTEM_PERMISSIONS
from docuflow.modules.identity.models import (
    AclEntry,
    Group,
    GroupMember,
    Role,
    RoleAssignment,
    RolePermission,
    User,
)
from docuflow.modules.identity.policy import PolicyService, bump_permission_epoch
from docuflow.modules.indexing.suggest import JOB_TYPE as FIELDS_JOB
from docuflow.modules.indexing.suggest import SuggestionService
from docuflow.modules.integrations.service import JOB_TYPE as WEBHOOK_JOB
from docuflow.modules.integrations.service import WebhookService
from docuflow.modules.notifications.service import (
    EMAIL_JOB,
    SLA_JOB,
    NotificationService,
    build_sender,
)
from docuflow.modules.repository.models import Cabinet
from docuflow.modules.repository.service import Destination, RepositoryService
from docuflow.modules.search.service import JOB_TYPE as SEARCH_JOB
from docuflow.modules.search.service import SearchService
from docuflow.modules.workflow.service import JOB_TYPE as WORKFLOW_JOB
from docuflow.modules.workflow.service import WorkflowService
from docuflow.platform.audit import AuditLog
from docuflow.platform.crypto import LocalKeyProvider
from docuflow.platform.db import Database, make_engine
from docuflow.platform.jobs import Dispatcher, HandlerRegistry, JobRunner, Maintenance
from docuflow.platform.models import Tenant
from docuflow.platform.storage import EncryptedObjects, S3ObjectStore

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
DATA = ROOT / ".data" / "dev"
DB_NAME = "docuflow_dev"
ROLE_PASSWORD = "dev-only-password"  # noqa: S105  # throwaway local database
SLUG = "demo"


class InMemoryRedis:
    """Just enough Redis for rate limiting and health checks."""

    def __init__(self, count_attempts: bool = True) -> None:
        # Automated tests sign in far more often than any person, all from one address.
        self._count_attempts = count_attempts
        # name -> (count, moment the window ends)
        self._counts: dict[str, tuple[int, float]] = {}

    def incr(self, name: str) -> int:
        if not self._count_attempts:
            return 1
        count, ends = self._counts.get(name, (0, float("inf")))
        if time.monotonic() >= ends:
            count, ends = 0, float("inf")
        self._counts[name] = (count + 1, ends)
        return count + 1

    def expire(self, name: str, seconds: int) -> bool:
        count, _ = self._counts.get(name, (0, 0.0))
        self._counts[name] = (count, time.monotonic() + seconds)
        return True

    def ping(self) -> bool:
        return True

    def exists(self, key: str) -> int:
        return 1


def stop_stale_database() -> None:
    """Stop a database left running by a previous run that was killed, so its files can go.

    A process that is terminated on Windows cannot shut its database down, and the leftover
    server keeps the data folder locked.
    """
    import pgserver

    data = DATA / "pg"
    if not (data / "postmaster.pid").exists():
        return
    binary = Path(pgserver.__file__).parent / "pginstall" / "bin"
    tool = binary / ("pg_ctl.exe" if sys.platform == "win32" else "pg_ctl")
    subprocess.run(  # noqa: S603
        [str(tool), "-D", str(data), "stop", "-m", "fast"], check=False, capture_output=True
    )


def start_database() -> dict[str, str]:
    import pgserver

    DATA.mkdir(parents=True, exist_ok=True)
    server = pgserver.get_server(DATA / "pg", cleanup_mode="stop")
    admin = make_url(server.get_uri()).set(drivername="postgresql")
    admin_url = admin.render_as_string(hide_password=False)
    with psycopg.connect(admin_url, autocommit=True) as connection:
        exists = connection.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", (DB_NAME,)
        ).fetchone()
        if not exists:
            connection.execute(f"CREATE DATABASE {DB_NAME}")
    target = admin.set(database=DB_NAME).render_as_string(hide_password=False)
    sql = (ROOT / "deploy" / "postgres" / "bootstrap.sql").read_text(encoding="utf-8")
    with psycopg.connect(target, autocommit=True) as connection:
        available = {
            row[0] for row in connection.execute("SELECT name FROM pg_available_extensions")
        }
        sql = "\n".join(
            line
            for line in sql.splitlines()
            if not (
                line.startswith("CREATE EXTENSION")
                and line.rstrip(";").split()[-1] not in available
            )
        )
        connection.execute(sql)
        for role in ("docuflow_migrator", "docuflow_app", "docuflow_platform"):
            connection.execute(f"ALTER ROLE {role} LOGIN PASSWORD '{ROLE_PASSWORD}'")
    urls = {
        role: admin.set(
            drivername="postgresql+psycopg",
            username=role,
            password=ROLE_PASSWORD,
            database=DB_NAME,
        ).render_as_string(hide_password=False)
        for role in ("docuflow_migrator", "docuflow_app", "docuflow_platform")
    }
    config = Config(str(BACKEND / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND / "migrations"))
    config.set_main_option("sqlalchemy.url", urls["docuflow_migrator"].replace("%", "%%"))
    command.upgrade(config, "head")
    # Keep the server object alive for the life of the process.
    urls["_server"] = server  # type: ignore[assignment]
    return urls


def master_key() -> bytes:
    path = DATA / "keys.json"
    if path.exists():
        return bytes.fromhex(json.loads(path.read_text(encoding="utf-8"))["master"])
    key = secrets.token_bytes(32)
    path.write_text(json.dumps({"master": key.hex()}), encoding="utf-8")
    return key


def build_services(urls: dict[str, str], count_attempts: bool = True) -> SimpleNamespace:
    moto = ThreadedMotoServer(ip_address="127.0.0.1", port=0, verbose=False)
    moto.start()
    port = moto.get_host_and_port()[1]
    store = S3ObjectStore(f"http://127.0.0.1:{port}", "us-east-1", "docuflow-dev", "dev", "dev")
    store.ensure_bucket()
    root = master_key()
    keys = LocalKeyProvider(root)
    return SimpleNamespace(
        settings=SimpleNamespace(
            log_level="INFO", environment="development", max_upload_bytes=100 * 1024 * 1024
        ),
        db=Database(make_engine(urls["docuflow_app"]), make_engine(urls["docuflow_platform"])),
        audit=AuditLog(root),
        keys=keys,
        redis=InMemoryRedis(count_attempts),
        store=store,
        objects=EncryptedObjects(store, keys),
    )


def seed(services: SimpleNamespace, fixed_password: str | None = None) -> None:
    db: Database = services.db
    with db.platform_session() as session:
        if session.scalar(select(Tenant.id).where(Tenant.slug == SLUG)):
            print("Demo organization already exists; leaving it as it is.")
            return
        tenant = Tenant(name="Demo Organization", slug=SLUG)
        session.add(tenant)
        session.flush()
        services.audit.record(session, tenant.id, "tenant.created", object_type="tenant")
        tenant_id = tenant.id

    credentials: list[tuple[str, str]] = []

    def new_user(session: Any, name: str) -> User:
        password = fixed_password or secrets.token_urlsafe(12) + "-A1"
        user = User(
            tenant_id=tenant_id,
            email=f"{name.lower()}@example.com",
            display_name=name,
            password_hash=security.hash_password(password),
        )
        session.add(user)
        session.flush()
        credentials.append((user.email, password))
        return user

    with db.tenant_session(tenant_id) as session:
        role = Role(tenant_id=tenant_id, name="Security admin", description="Full administration")
        session.add(role)
        session.flush()
        session.add_all(
            RolePermission(tenant_id=tenant_id, role_id=role.id, permission=code)
            for code in SYSTEM_PERMISSIONS
        )
        admin = new_user(session, "Admin")
        felix = new_user(session, "Felix")
        hana = new_user(session, "Hana")
        session.add(RoleAssignment(tenant_id=tenant_id, role_id=role.id, user_id=admin.id))
        finance_team = Group(tenant_id=tenant_id, name="Finance team")
        hr_team = Group(tenant_id=tenant_id, name="HR team")
        session.add_all([finance_team, hr_team])
        session.flush()
        session.add_all(
            [
                GroupMember(tenant_id=tenant_id, group_id=finance_team.id, user_id=felix.id),
                GroupMember(tenant_id=tenant_id, group_id=hr_team.id, user_id=hana.id),
            ]
        )
        bump_permission_epoch(session, tenant_id)

    repo = RepositoryService(services.audit, PolicyService())
    boss = Principal(tenant_id, admin.id, "session")
    with db.tenant_session(tenant_id) as session:
        finance = repo.create_cabinet(session, boss, "Finance", "Invoices, budgets and payments")
        hr = repo.create_cabinet(session, boss, "HR", "People, contracts and payroll")
        invoices = repo.create_folder(
            session, boss, Destination(cabinet_id=finance.id), "Supplier Invoices"
        )
        year = repo.create_folder(session, boss, Destination(folder_id=invoices.id), "2026")
        november = repo.create_folder(session, boss, Destination(folder_id=year.id), "November")
        repo.create_folder(session, boss, Destination(cabinet_id=finance.id), "Customer Invoices")
        repo.create_folder(session, boss, Destination(cabinet_id=finance.id), "Purchase Orders")
        employees = repo.create_folder(
            session, boss, Destination(cabinet_id=hr.id), "Employee Files"
        )
        repo.create_folder(session, boss, Destination(folder_id=employees.id), "2026")
        repo.file_document(
            session, boss, Destination(folder_id=november.id), "Invoice ABC-1042.pdf", 184_320
        )
        repo.file_document(
            session, boss, Destination(folder_id=november.id), "Invoice ABC-1043.pdf", 201_760
        )
        repo.file_document(
            session, boss, Destination(folder_id=employees.id), "Contract - Hana.pdf", 96_512
        )

    with db.tenant_session(tenant_id) as session:
        for team, cabinet_name, extra in (
            (
                finance_team,
                "Finance",
                ("create", "edit_metadata", "annotate", "version", "download"),
            ),
            (hr_team, "HR", ("create", "annotate")),
        ):
            cabinet = session.scalar(select(Cabinet).where(Cabinet.name == cabinet_name))
            assert cabinet is not None
            for permission in ("read", "preview", *extra):
                session.add(
                    AclEntry(
                        tenant_id=tenant_id,
                        anchor_id=cabinet.acl_anchor_id,
                        principal_type="group",
                        principal_id=team.id,
                        permission=permission,
                        effect="allow",
                    )
                )
        for cabinet_name in ("Finance", "HR"):
            cabinet = session.scalar(select(Cabinet).where(Cabinet.name == cabinet_name))
            assert cabinet is not None
            session.add(
                AclEntry(
                    tenant_id=tenant_id,
                    anchor_id=cabinet.acl_anchor_id,
                    principal_type="user",
                    principal_id=admin.id,
                    permission="dispose",
                    effect="allow",
                )
            )
        bump_permission_epoch(session, tenant_id)

    lines = [f"Organization: {SLUG}"] + [
        f"{email}  /  {password}" for email, password in credentials
    ]
    (DATA / "credentials.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("Seeded the demo organization. Sign-in details:\n  " + "\n  ".join(lines))


def run_jobs_inline(services: SimpleNamespace, stop: threading.Event) -> None:
    """Stands in for the dispatcher and the worker, which the demo does not start.

    It uses the real outbox, dispatcher and job runner, so imports behave as in production,
    one job at a time.
    """
    registry = HandlerRegistry()
    policy = PolicyService()
    repo = RepositoryService(services.audit, policy)
    built = build_documents(services, repo, policy)
    registry.register(JOB_TYPE)(lambda job: built.imports.run(services.db, job))
    registry.register(EXTRACT_JOB)(lambda job: built.text.run(services.db, job))
    registry.register(FIELDS_JOB)(
        lambda job: SuggestionService(services.audit, policy, repo).run(services.db, job)
    )
    registry.register(COMPRESS_JOB)(
        lambda job: CompressionService(services.audit, policy, repo, services.objects).run(
            services.db, job
        )
    )
    hooks = WebhookService(services.audit, policy, services.keys)
    registry.register(WEBHOOK_JOB)(lambda job: hooks.deliver(services.db, job))
    registry.register(SEARCH_JOB)(
        lambda job: SearchService(services.audit, policy).run(services.db, job)
    )
    registry.register(WORKFLOW_JOB)(
        lambda job: WorkflowService(services.audit, policy, repo).run(services.db, job)
    )
    notes = NotificationService(services.audit, policy, build_sender(object()))
    registry.register(SLA_JOB)(lambda job: notes.run_sla(services.db, job))
    registry.register(EMAIL_JOB)(lambda job: notes.run_email(services.db, job))
    runner = JobRunner(services.db, registry, services.audit, 1)
    maintenance = Maintenance(services.db, services.audit, 300)
    pending: list[dict[str, Any]] = []
    dispatcher = Dispatcher(services.db, lambda _topic, payload: pending.append(payload))
    while not stop.wait(0.5):
        try:
            maintenance.fire_timers()
            dispatcher.publish_batch()
            while pending:
                payload = pending.pop(0)
                runner.run(uuid.UUID(payload["tenant_id"]), uuid.UUID(payload["job_id"]))
        except Exception as exc:  # the demo keeps serving whatever happens to a job
            print(f"background job failed: {exc}", file=sys.stderr)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--seed", action="store_true", help="create the demo organization once")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--data-dir", default=None, help="where to keep data (default .data/dev)")
    parser.add_argument("--fresh", action="store_true", help="delete the data folder first")
    parser.add_argument(
        "--password",
        default=None,
        help="use one known password for every seeded user, for automated tests only",
    )
    args = parser.parse_args()

    global DATA
    if args.data_dir:
        DATA = Path(args.data_dir).resolve()
    if args.fresh and DATA.exists():
        # Refuse to delete anything outside the repository's ignored data folder.
        if (ROOT / ".data").resolve() not in DATA.parents:
            parser.error("--fresh only works inside the repository's .data folder")
        stop_stale_database()
        shutil.rmtree(DATA)

    urls = start_database()
    # A fixed password means an automated run, which must not trip the sign-in rate limit.
    services = build_services(urls, count_attempts=args.password is None)
    if args.seed:
        seed(services, args.password)
    app = create_app(services)  # type: ignore[arg-type]
    threading.Thread(
        target=run_jobs_inline, args=(services, threading.Event()), daemon=True
    ).start()
    print(f"API on http://127.0.0.1:{args.port}  (docs at /api/v1/docs)")
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
