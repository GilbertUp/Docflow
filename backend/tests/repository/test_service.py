"""F02 against a real database: structure, permissions, moves, concurrency, archive, delete."""

from __future__ import annotations

import threading
import uuid

import pytest
from sqlalchemy import select

from docuflow.modules.identity.models import AclAnchor, AclEntry
from docuflow.modules.identity.policy import PolicyService
from docuflow.modules.repository.models import Cabinet, Folder
from docuflow.modules.repository.service import (
    MAX_DEPTH,
    Destination,
    RepositoryService,
)
from docuflow.platform.audit import AuditLog
from docuflow.platform.db import Database
from docuflow.platform.errors import Conflict, DomainError, Forbidden, NotFound
from tests.identity.support import (
    audit_actions,
    audit_events,
    join_group,
    new_group,
    new_user,
    principal,
)
from tests.repository.support import (
    allow,
    anchor_exists,
    boss_of,
    cabinet_anchor,
    document_row,
    folder_anchor,
    folder_row,
    in_cabinet,
    in_folder,
    make_cabinet,
    make_document,
    make_folder,
    sees_document,
    sees_folder,
)

pytestmark = pytest.mark.db


class Finance:
    """Finance > Supplier Invoices > 2026 > November, plus an HR cabinet."""

    def __init__(self, db: Database, repo: RepositoryService, tenant: uuid.UUID) -> None:
        self.tenant = tenant
        self.boss = boss_of(db, tenant)
        self.finance = make_cabinet(db, repo, self.boss, "Finance")
        self.hr = make_cabinet(db, repo, self.boss, "HR")
        self.invoices = make_folder(
            db, repo, self.boss, in_cabinet(self.finance), "Supplier Invoices"
        )
        self.year = make_folder(db, repo, self.boss, in_folder(self.invoices), "2026")
        self.november = make_folder(db, repo, self.boss, in_folder(self.year), "November")


@pytest.fixture
def world(db: Database, repo: RepositoryService, tenant: uuid.UUID) -> Finance:
    return Finance(db, repo, tenant)


def test_creating_a_cabinet_needs_the_permission(
    db: Database, repo: RepositoryService, tenant: uuid.UUID
) -> None:
    nobody = principal(tenant, new_user(db, tenant, "nobody"))
    with pytest.raises(Forbidden) as caught, db.tenant_session(tenant) as session:
        repo.create_cabinet(session, nobody, "Sneaky", "")
    assert caught.value.details == {"required": "cabinets.manage"}


def test_the_creator_owns_the_cabinet_and_strangers_cannot_see_it(
    db: Database, repo: RepositoryService, tenant: uuid.UUID, world: Finance
) -> None:
    outsider = principal(tenant, new_user(db, tenant, "outsider"))
    with db.tenant_session(tenant) as session:
        mine, _ = repo.list_cabinets(
            session, world.boss, include_archived=False, limit=50, offset=0
        )
        theirs, total = repo.list_cabinets(
            session, outsider, include_archived=False, limit=50, offset=0
        )
    assert [c.name for c in mine] == ["Finance", "HR"]
    assert (theirs, total) == ([], 0)
    with pytest.raises(NotFound), db.tenant_session(tenant) as session:
        repo.get_cabinet(session, outsider, world.hr)


def test_cabinet_names_are_unique_ignoring_case(
    db: Database, repo: RepositoryService, tenant: uuid.UUID, world: Finance
) -> None:
    with pytest.raises(Conflict), db.tenant_session(tenant) as session:
        repo.create_cabinet(session, world.boss, "finance", "")


def test_nested_folders_persist_with_depth_and_breadcrumb(
    db: Database, repo: RepositoryService, tenant: uuid.UUID, world: Finance
) -> None:
    assert [
        folder_row(db, tenant, f).depth for f in (world.invoices, world.year, world.november)
    ] == [1, 2, 3]
    with db.tenant_session(tenant) as session:
        _, crumbs = repo.get_folder(session, world.boss, world.november)
    assert [c.name for c in crumbs] == ["Finance", "Supplier Invoices", "2026", "November"]
    assert [c.kind for c in crumbs] == ["cabinet", "folder", "folder", "folder"]


def test_folder_names_are_unique_per_parent_but_reusable_elsewhere(
    db: Database, repo: RepositoryService, tenant: uuid.UUID, world: Finance
) -> None:
    with pytest.raises(Conflict), db.tenant_session(tenant) as session:
        repo.create_folder(session, world.boss, in_folder(world.invoices), "2026")
    with pytest.raises(Conflict), db.tenant_session(tenant) as session:
        repo.create_folder(session, world.boss, in_cabinet(world.finance), "supplier invoices")
    other = make_folder(db, repo, world.boss, in_cabinet(world.finance), "Customer Invoices")
    make_folder(db, repo, world.boss, in_folder(other), "2026")
    make_folder(db, repo, world.boss, in_cabinet(world.hr), "Supplier Invoices")


@pytest.mark.parametrize("name", ["", "   ", "a/b", "a\\b", "..", ".", "x" * 201, "line\nbreak"])
def test_bad_names_are_rejected(
    db: Database, repo: RepositoryService, tenant: uuid.UUID, world: Finance, name: str
) -> None:
    with pytest.raises(DomainError), db.tenant_session(tenant) as session:
        repo.create_folder(session, world.boss, in_cabinet(world.finance), name)


def test_nesting_depth_is_limited(
    db: Database, repo: RepositoryService, tenant: uuid.UUID, world: Finance
) -> None:
    parent = world.november  # already at depth 3
    with db.tenant_session(tenant) as session:
        for level in range(4, MAX_DEPTH + 1):
            parent = repo.create_folder(session, world.boss, in_folder(parent), f"level {level}").id
    with pytest.raises(DomainError), db.tenant_session(tenant) as session:
        repo.create_folder(session, world.boss, in_folder(parent), "too deep")


def test_cabinet_permissions_are_inherited_by_folders_with_exceptions(
    db: Database, repo: RepositoryService, tenant: uuid.UUID, world: Finance
) -> None:
    team = new_group(db, tenant, "Finance team")
    felix = new_user(db, tenant, "felix")
    join_group(db, tenant, team, felix)
    from tests.identity.support import grant

    grant(db, tenant, cabinet_anchor(db, tenant, world.finance), "group", team, "read")
    assert all(
        sees_folder(db, repo, tenant, felix, f)
        for f in (world.invoices, world.year, world.november)
    )
    allow(db, tenant, folder_anchor(db, tenant, world.year), felix, "read", effect="deny")
    assert sees_folder(db, repo, tenant, felix, world.invoices)
    assert not sees_folder(db, repo, tenant, felix, world.year)
    assert not sees_folder(db, repo, tenant, felix, world.november)
    allow(db, tenant, folder_anchor(db, tenant, world.november), felix, "read")
    assert sees_folder(db, repo, tenant, felix, world.november)  # a deeper allow is an exception


def test_creating_a_folder_needs_create_permission(
    db: Database, repo: RepositoryService, tenant: uuid.UUID, world: Finance
) -> None:
    reader = new_user(db, tenant, "reader")
    allow(db, tenant, cabinet_anchor(db, tenant, world.finance), reader, "read")
    with pytest.raises(Forbidden) as caught, db.tenant_session(tenant) as session:
        repo.create_folder(session, principal(tenant, reader), in_cabinet(world.finance), "Nope")
    assert caught.value.details == {"required": "create"}
    allow(db, tenant, cabinet_anchor(db, tenant, world.finance), reader, "create")
    make_folder(db, repo, principal(tenant, reader), in_cabinet(world.finance), "Allowed now")


def test_moving_a_folder_changes_inherited_access_and_keeps_its_documents(
    db: Database, repo: RepositoryService, tenant: uuid.UUID, world: Finance
) -> None:
    fin_team, hr_team = new_group(db, tenant, "Finance team"), new_group(db, tenant, "HR team")
    felix, hana = new_user(db, tenant, "felix"), new_user(db, tenant, "hana")
    join_group(db, tenant, fin_team, felix)
    join_group(db, tenant, hr_team, hana)
    from tests.identity.support import grant

    grant(db, tenant, cabinet_anchor(db, tenant, world.finance), "group", fin_team, "read")
    grant(db, tenant, cabinet_anchor(db, tenant, world.hr), "group", hr_team, "read")
    payroll = make_folder(db, repo, world.boss, in_cabinet(world.hr), "Payroll")
    sub = make_folder(db, repo, world.boss, in_folder(payroll), "2026")
    payslip = make_document(db, repo, world.boss, in_folder(sub), "payslip.pdf", 1000)
    assert sees_document(db, repo, tenant, hana, payslip)
    assert not sees_document(db, repo, tenant, felix, payslip)

    with db.tenant_session(tenant) as session:
        moved = repo.move_folder(session, world.boss, payroll, in_cabinet(world.finance), 1)
        assert moved.row_version == 2

    assert sees_document(db, repo, tenant, felix, payslip)
    assert not sees_document(db, repo, tenant, hana, payslip)
    document = document_row(db, tenant, payslip)
    assert document.id == payslip
    assert document.cabinet_id == world.finance
    assert folder_row(db, tenant, sub).cabinet_id == world.finance
    assert (folder_row(db, tenant, payroll).depth, folder_row(db, tenant, sub).depth) == (1, 2)
    event = audit_events(db, tenant, "folder.moved")[-1]
    assert event.details["folders_in_subtree"] == 2


def test_moving_under_another_folder_shifts_depths(
    db: Database, repo: RepositoryService, tenant: uuid.UUID, world: Finance
) -> None:
    extra = make_folder(db, repo, world.boss, in_cabinet(world.finance), "Archive 2025")
    leaf = make_folder(db, repo, world.boss, in_folder(extra), "Q4")
    with db.tenant_session(tenant) as session:
        repo.move_folder(session, world.boss, extra, in_folder(world.november), 1)
    assert (folder_row(db, tenant, extra).depth, folder_row(db, tenant, leaf).depth) == (4, 5)


def test_a_folder_cannot_move_into_itself_or_its_descendants(
    db: Database, repo: RepositoryService, tenant: uuid.UUID, world: Finance
) -> None:
    for target in (world.invoices, world.year, world.november):
        with pytest.raises(Conflict), db.tenant_session(tenant) as session:
            repo.move_folder(session, world.boss, world.invoices, in_folder(target), 1)


def test_moving_needs_move_on_the_source_and_create_on_the_destination(
    db: Database, repo: RepositoryService, tenant: uuid.UUID, world: Finance
) -> None:
    mover = new_user(db, tenant, "mover")
    who = principal(tenant, mover)
    allow(db, tenant, cabinet_anchor(db, tenant, world.finance), mover, "read")
    with pytest.raises(Forbidden) as no_move, db.tenant_session(tenant) as session:
        repo.move_folder(session, who, world.november, in_folder(world.invoices), 1)
    assert no_move.value.details == {"required": "move"}

    allow(db, tenant, cabinet_anchor(db, tenant, world.finance), mover, "move")
    with pytest.raises(Forbidden) as no_create, db.tenant_session(tenant) as session:
        repo.move_folder(session, who, world.november, in_folder(world.invoices), 1)
    assert no_create.value.details == {"required": "create"}

    with pytest.raises(NotFound), db.tenant_session(tenant) as session:
        repo.move_folder(session, who, world.november, in_cabinet(world.hr), 1)  # cannot read HR


def test_a_failed_move_changes_nothing(
    db: Database, repo: RepositoryService, tenant: uuid.UUID, world: Finance
) -> None:
    clash = make_folder(db, repo, world.boss, in_cabinet(world.hr), "2026")
    twin = make_folder(db, repo, world.boss, in_cabinet(world.finance), "2026 again")
    with db.tenant_session(tenant) as session:
        repo.update_folder(session, world.boss, twin, row_version=1, name="2026")
    with pytest.raises(Conflict), db.tenant_session(tenant) as session:
        repo.move_folder(session, world.boss, twin, in_cabinet(world.hr), 2)
    row = folder_row(db, tenant, twin)
    assert (row.cabinet_id, row.row_version) == (world.finance, 2)
    assert clash


def test_moving_to_the_same_place_does_nothing(
    db: Database, repo: RepositoryService, tenant: uuid.UUID, world: Finance
) -> None:
    before = len(audit_events(db, tenant, "folder.moved"))
    with db.tenant_session(tenant) as session:
        same = repo.move_folder(session, world.boss, world.year, in_folder(world.invoices), 1)
    assert same.row_version == 1
    assert len(audit_events(db, tenant, "folder.moved")) == before


def test_stale_edits_are_rejected(
    db: Database, repo: RepositoryService, tenant: uuid.UUID, world: Finance
) -> None:
    with db.tenant_session(tenant) as session:
        repo.update_folder(session, world.boss, world.year, row_version=1, name="2026 H1")
    with pytest.raises(Conflict), db.tenant_session(tenant) as session:
        repo.update_folder(session, world.boss, world.year, row_version=1, name="2026 H2")
    with pytest.raises(Conflict), db.tenant_session(tenant) as session:
        repo.move_folder(session, world.boss, world.year, in_cabinet(world.hr), 1)


def run_in_threads(jobs: list) -> list[str]:
    outcomes: list[str] = [""] * len(jobs)
    gate = threading.Barrier(len(jobs))

    def runner(index: int) -> None:
        gate.wait()
        try:
            jobs[index]()
            outcomes[index] = "ok"
        except Conflict:
            outcomes[index] = "conflict"

    threads = [threading.Thread(target=runner, args=(i,)) for i in range(len(jobs))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return outcomes


def test_racing_moves_cannot_create_a_loop(
    db: Database, repo: RepositoryService, tenant: uuid.UUID, world: Finance
) -> None:
    """Two moves that reference each other. The lock test below covers the harder case."""
    first = make_folder(db, repo, world.boss, in_cabinet(world.finance), "A")
    second = make_folder(db, repo, world.boss, in_cabinet(world.finance), "B")

    def move(source: uuid.UUID, target: uuid.UUID):
        def job() -> None:
            with db.tenant_session(tenant) as session:
                repo.move_folder(session, world.boss, source, in_folder(target), 1)

        return job

    outcomes = run_in_threads([move(first, second), move(second, first)])
    assert sorted(outcomes) == ["conflict", "ok"]
    parents = {folder_row(db, tenant, f).parent_id for f in (first, second)}
    assert None in parents  # one of them is still at the top
    assert len(parents) == 2  # and the other sits under it, with no cycle


def test_a_second_structural_change_waits_for_the_first_to_finish(
    db: Database, repo: RepositoryService, tenant: uuid.UUID, world: Finance
) -> None:
    """Deterministic proof of the lock, using the one race that touches no shared rows.

    P holds Q and R holds S. Moving P under S while R moves under Q would form the loop
    P > S > R > Q > P, yet each move only changes its own folder, so the database's row locks
    would not stop it. Only the organization-wide structure lock does.
    """
    cab = in_cabinet(world.finance)
    p = make_folder(db, repo, world.boss, cab, "P")
    q = make_folder(db, repo, world.boss, in_folder(p), "Q")
    r = make_folder(db, repo, world.boss, cab, "R")
    s = make_folder(db, repo, world.boss, in_folder(r), "S")
    outcome: dict[str, str] = {}

    def move_r_under_q() -> None:
        try:
            with db.tenant_session(tenant) as session:
                repo.move_folder(session, world.boss, r, in_folder(q), 1)
            outcome["result"] = "moved"
        except Conflict:
            outcome["result"] = "conflict"

    with db.tenant_session(tenant) as session:
        repo.move_folder(session, world.boss, p, in_folder(s), 1)  # not yet committed
        thread = threading.Thread(target=move_r_under_q)
        thread.start()
        thread.join(timeout=1.5)
        assert thread.is_alive(), "the second change did not wait for the first"
    thread.join(timeout=30)
    assert outcome == {"result": "conflict"}  # it ran afterwards, saw the new tree, and refused
    parents = {
        name: folder_row(db, tenant, folder).parent_id for name, folder in (("P", p), ("R", r))
    }
    assert parents["R"] is None  # no loop was formed


def test_racing_renames_let_only_one_win(
    db: Database, repo: RepositoryService, tenant: uuid.UUID, world: Finance
) -> None:
    def rename(name: str):
        def job() -> None:
            with db.tenant_session(tenant) as session:
                repo.update_folder(session, world.boss, world.year, row_version=1, name=name)

        return job

    outcomes = run_in_threads([rename("Left"), rename("Right")])
    assert sorted(outcomes) == ["conflict", "ok"]
    assert folder_row(db, tenant, world.year).row_version == 2


def test_archived_folders_block_new_content_below_them(
    db: Database, repo: RepositoryService, tenant: uuid.UUID, world: Finance
) -> None:
    with db.tenant_session(tenant) as session:
        repo.update_folder(session, world.boss, world.invoices, row_version=1, status="archived")
    for destination in (in_folder(world.invoices), in_folder(world.november)):
        with pytest.raises(Conflict), db.tenant_session(tenant) as session:
            repo.create_folder(session, world.boss, destination, "New")
        with pytest.raises(Conflict), db.tenant_session(tenant) as session:
            repo.file_document(session, world.boss, destination, "New.pdf")
    with db.tenant_session(tenant) as session:
        visible = repo.children(
            session,
            world.boss,
            in_cabinet(world.finance),
            include_archived=False,
            limit=10,
            offset=0,
        )
        everything = repo.children(
            session,
            world.boss,
            in_cabinet(world.finance),
            include_archived=True,
            limit=10,
            offset=0,
        )
    assert visible.folders == []
    assert [f.name for f in everything.folders] == ["Supplier Invoices"]


def test_archiving_needs_delete_permission(
    db: Database, repo: RepositoryService, tenant: uuid.UUID, world: Finance
) -> None:
    editor = new_user(db, tenant, "editor")
    allow(db, tenant, cabinet_anchor(db, tenant, world.finance), editor, "read", "edit_metadata")
    with pytest.raises(Forbidden) as caught, db.tenant_session(tenant) as session:
        repo.update_folder(
            session, principal(tenant, editor), world.year, row_version=1, status="archived"
        )
    assert caught.value.details == {"required": "delete"}


def test_only_empty_folders_can_be_deleted_and_deletion_cleans_up(
    db: Database, repo: RepositoryService, tenant: uuid.UUID, world: Finance
) -> None:
    with pytest.raises(Conflict), db.tenant_session(tenant) as session:
        repo.delete_folder(session, world.boss, world.year)  # has a subfolder
    make_document(db, repo, world.boss, in_folder(world.november), "invoice.pdf")
    with pytest.raises(Conflict), db.tenant_session(tenant) as session:
        repo.delete_folder(session, world.boss, world.november)  # has a document
    empty = make_folder(db, repo, world.boss, in_folder(world.year), "Empty")
    anchor = folder_anchor(db, tenant, empty)
    with db.tenant_session(tenant) as session:
        repo.delete_folder(session, world.boss, empty)
    assert not anchor_exists(db, tenant, anchor)
    with db.tenant_session(tenant) as session:
        assert session.scalars(select(AclEntry).where(AclEntry.anchor_id == anchor)).all() == []
    assert not sees_folder(db, repo, tenant, world.boss.user_id, empty)
    assert audit_events(db, tenant, "folder.deleted")[-1].details["name"] == "Empty"


def test_only_empty_cabinets_can_be_deleted(
    db: Database, repo: RepositoryService, tenant: uuid.UUID, world: Finance
) -> None:
    with pytest.raises(Conflict), db.tenant_session(tenant) as session:
        repo.delete_cabinet(session, world.boss, world.finance)
    with db.tenant_session(tenant) as session:
        repo.delete_cabinet(session, world.boss, world.hr)
    with pytest.raises(NotFound), db.tenant_session(tenant) as session:
        repo.get_cabinet(session, world.boss, world.hr)


def test_copying_copies_structure_but_not_documents_or_permissions(
    db: Database, repo: RepositoryService, tenant: uuid.UUID, world: Finance
) -> None:
    make_document(db, repo, world.boss, in_folder(world.november), "invoice.pdf")
    with db.tenant_session(tenant) as session:
        root, copied, skipped = repo.copy_folder(
            session, world.boss, world.invoices, in_cabinet(world.hr)
        )
        root_id = root.id
    assert (copied, skipped) == (3, 0)
    assert root_id != world.invoices
    with db.tenant_session(tenant) as session:
        names = sorted(
            f.name for f in session.scalars(select(Folder).where(Folder.cabinet_id == world.hr))
        )
        counts = repo.stats(session, world.boss, in_cabinet(world.hr))
    assert names == ["2026", "November", "Supplier Invoices"]
    assert counts.subtree_documents == 0  # documents are not copied


def test_copying_into_the_same_place_picks_a_free_name(
    db: Database, repo: RepositoryService, tenant: uuid.UUID, world: Finance
) -> None:
    for expected in ("Supplier Invoices (copy)", "Supplier Invoices (copy 2)"):
        with db.tenant_session(tenant) as session:
            root, *_ = repo.copy_folder(
                session, world.boss, world.invoices, in_cabinet(world.finance)
            )
            assert root.name == expected


def test_copy_skips_subfolders_the_caller_cannot_read_and_needs_create_rights(
    db: Database, repo: RepositoryService, tenant: uuid.UUID, world: Finance
) -> None:
    copier = new_user(db, tenant, "copier")
    allow(db, tenant, cabinet_anchor(db, tenant, world.finance), copier, "read")
    allow(db, tenant, folder_anchor(db, tenant, world.year), copier, "read", effect="deny")
    who = principal(tenant, copier)
    with pytest.raises(Forbidden), db.tenant_session(tenant) as session:
        repo.copy_folder(session, who, world.invoices, in_cabinet(world.finance))
    allow(db, tenant, cabinet_anchor(db, tenant, world.finance), copier, "create")
    with db.tenant_session(tenant) as session:
        _, copied, skipped = repo.copy_folder(
            session, who, world.invoices, in_cabinet(world.finance)
        )
    assert (copied, skipped) == (1, 2)  # the hidden folder and everything below it


def test_counts_only_include_what_the_caller_may_read(
    db: Database, repo: RepositoryService, tenant: uuid.UUID, world: Finance
) -> None:
    make_document(db, repo, world.boss, in_folder(world.november), "visible.pdf", 100)
    secret = make_document(db, repo, world.boss, in_folder(world.november), "secret.pdf", 900)
    guest = new_user(db, tenant, "guest")
    allow(db, tenant, cabinet_anchor(db, tenant, world.finance), guest, "read")
    with db.tenant_session(tenant) as session:
        repo.ensure_document_anchor(session, world.boss, secret)
    with db.tenant_session(tenant) as session:
        anchor = document_row(db, tenant, secret).acl_anchor_id
    allow(db, tenant, anchor, guest, "read", effect="deny")

    with db.tenant_session(tenant) as session:
        everything = repo.stats(session, world.boss, in_folder(world.invoices))
        limited = repo.stats(session, principal(tenant, guest), in_folder(world.invoices))
    assert (everything.subtree_documents, everything.storage_bytes) == (2, 1000)
    assert (limited.subtree_documents, limited.storage_bytes) == (1, 100)
    assert [d.title for d in limited.recent] == ["visible.pdf"]
    assert limited.subfolders == 1


def test_breadcrumb_hides_names_the_caller_cannot_read(
    db: Database, repo: RepositoryService, tenant: uuid.UUID, world: Finance
) -> None:
    visitor = new_user(db, tenant, "visitor")
    allow(db, tenant, folder_anchor(db, tenant, world.year), visitor, "read")
    with db.tenant_session(tenant) as session:
        _, crumbs = repo.get_folder(session, principal(tenant, visitor), world.november)
    assert [c.name for c in crumbs] == ["(restricted)", "(restricted)", "2026", "November"]
    assert crumbs[0].id is None


def test_documents_move_without_changing_identity(
    db: Database, repo: RepositoryService, tenant: uuid.UUID, world: Finance
) -> None:
    document = make_document(db, repo, world.boss, in_folder(world.november), "invoice.pdf")
    with db.tenant_session(tenant) as session:
        moved = repo.move_document(session, world.boss, document, in_cabinet(world.hr), 1)
        assert moved.id == document
    row = document_row(db, tenant, document)
    assert (row.cabinet_id, row.folder_id, row.row_version) == (world.hr, None, 2)
    assert row.acl_anchor_id == cabinet_anchor(db, tenant, world.hr)
    event = audit_events(db, tenant, "document.moved")[-1]
    assert event.object_id == str(document)


def test_document_moves_need_the_right_permissions(
    db: Database, repo: RepositoryService, tenant: uuid.UUID, world: Finance
) -> None:
    document = make_document(db, repo, world.boss, in_folder(world.november), "invoice.pdf")
    mover = new_user(db, tenant, "mover")
    allow(db, tenant, cabinet_anchor(db, tenant, world.finance), mover, "read")
    allow(db, tenant, cabinet_anchor(db, tenant, world.hr), mover, "read", "create")
    with pytest.raises(Forbidden), db.tenant_session(tenant) as session:
        repo.move_document(session, principal(tenant, mover), document, in_cabinet(world.hr), 1)
    allow(db, tenant, cabinet_anchor(db, tenant, world.finance), mover, "move")
    with db.tenant_session(tenant) as session:
        repo.move_document(session, principal(tenant, mover), document, in_cabinet(world.hr), 1)


def test_a_document_with_its_own_permissions_keeps_them_when_moved(
    db: Database, repo: RepositoryService, tenant: uuid.UUID, world: Finance
) -> None:
    document = make_document(db, repo, world.boss, in_folder(world.november), "contract.pdf")
    with db.tenant_session(tenant) as session:
        own = repo.ensure_document_anchor(session, world.boss, document)
        again = repo.ensure_document_anchor(session, world.boss, document)
    assert own == again
    guest = new_user(db, tenant, "guest")
    allow(db, tenant, cabinet_anchor(db, tenant, world.finance), guest, "read")
    allow(db, tenant, own, guest, "read", effect="deny")
    assert not sees_document(db, repo, tenant, guest, document)
    with db.tenant_session(tenant) as session:
        repo.move_document(session, world.boss, document, in_folder(world.invoices), 1)
    assert not sees_document(db, repo, tenant, guest, document)  # the exception travelled with it
    with db.tenant_session(tenant) as session:
        anchor = session.get(AclAnchor, own)
        assert anchor is not None
        assert anchor.parent_anchor_id == folder_anchor(db, tenant, world.invoices)


def test_giving_a_document_its_own_permissions_needs_manage_acl(
    db: Database, repo: RepositoryService, tenant: uuid.UUID, world: Finance
) -> None:
    document = make_document(db, repo, world.boss, in_folder(world.november), "contract.pdf")
    reader = new_user(db, tenant, "reader")
    allow(db, tenant, cabinet_anchor(db, tenant, world.finance), reader, "read")
    with pytest.raises(Forbidden), db.tenant_session(tenant) as session:
        repo.ensure_document_anchor(session, principal(tenant, reader), document)


def test_filing_a_document_needs_create_permission(
    db: Database, repo: RepositoryService, tenant: uuid.UUID, world: Finance
) -> None:
    reader = new_user(db, tenant, "reader")
    allow(db, tenant, cabinet_anchor(db, tenant, world.finance), reader, "read")
    with pytest.raises(Forbidden), db.tenant_session(tenant) as session:
        repo.file_document(session, principal(tenant, reader), in_folder(world.year), "x.pdf")


def test_browsing_lists_only_readable_children_with_paged_documents(
    db: Database, repo: RepositoryService, tenant: uuid.UUID, world: Finance
) -> None:
    for index in range(5):
        make_document(db, repo, world.boss, in_folder(world.november), f"invoice {index}.pdf")
    with db.tenant_session(tenant) as session:
        page = repo.children(
            session,
            world.boss,
            in_folder(world.november),
            include_archived=False,
            limit=2,
            offset=2,
        )
    assert (len(page.documents), page.total_documents) == (2, 5)
    assert page.folders == []


def test_nothing_is_reachable_from_another_tenant(
    db: Database,
    repo: RepositoryService,
    tenant: uuid.UUID,
    other_tenant: uuid.UUID,
    world: Finance,
) -> None:
    foreign = boss_of(db, other_tenant)
    for call in (
        lambda s: repo.get_folder(s, foreign, world.year),
        lambda s: repo.get_cabinet(s, foreign, world.finance),
        lambda s: repo.create_folder(s, foreign, in_folder(world.year), "x"),
        lambda s: repo.move_folder(s, foreign, world.year, in_cabinet(world.hr), 1),
    ):
        with pytest.raises(NotFound), db.tenant_session(other_tenant) as session:
            call(session)


def test_everything_is_audited_and_the_chain_verifies(
    db: Database, repo: RepositoryService, audit: AuditLog, tenant: uuid.UUID, world: Finance
) -> None:
    document = make_document(db, repo, world.boss, in_folder(world.november), "invoice.pdf")
    with db.tenant_session(tenant) as session:
        repo.move_document(session, world.boss, document, in_cabinet(world.hr), 1)
        repo.update_folder(session, world.boss, world.year, row_version=1, name="2026 H1")
        repo.update_cabinet(session, world.boss, world.hr, row_version=1, name="People")
    actions = set(audit_actions(db, tenant))
    for expected in (
        "cabinet.created",
        "cabinet.updated",
        "folder.created",
        "folder.updated",
        "document.filed",
        "document.moved",
    ):
        assert expected in actions
    with db.tenant_session(tenant) as session:
        assert audit.verify(session, tenant).ok


def test_unused_destination_validation() -> None:
    with pytest.raises(DomainError):
        Destination().validate()
    with pytest.raises(DomainError):
        Destination(uuid.uuid4(), uuid.uuid4()).validate()


def test_policy_service_is_shared_correctly(policy: PolicyService, repo: RepositoryService) -> None:
    assert isinstance(policy, PolicyService)
    assert Cabinet.__tablename__ == "cabinet"
    assert repo is not None
