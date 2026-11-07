"""ADR 0004 rules, tested as pure logic."""

from __future__ import annotations

import uuid

import pytest

from docuflow.modules.identity.policy import AnchorEntry, AnchorTree

TENANT, CABINET, FOLDER, SUBFOLDER, OTHER = (uuid.uuid4() for _ in range(5))
PARENTS: dict[uuid.UUID, uuid.UUID | None] = {
    TENANT: None,
    CABINET: TENANT,
    FOLDER: CABINET,
    SUBFOLDER: FOLDER,
    OTHER: TENANT,
}
USER = ("user", uuid.uuid4())
GROUP = ("group", uuid.uuid4())


def tree(*entries: tuple[uuid.UUID, str, str]) -> AnchorTree:
    return AnchorTree(
        PARENTS, [AnchorEntry(anchor, USER, "read", effect) for anchor, _, effect in entries]
    )


def test_nothing_is_allowed_by_default() -> None:
    decision = tree().decide(SUBFOLDER, "read")
    assert decision.allowed is False
    assert decision.reason == "no matching entry"


def test_allow_is_inherited_downwards() -> None:
    decision = tree((CABINET, "read", "allow")).decide(SUBFOLDER, "read")
    assert decision.allowed is True
    assert decision.source_anchor_id == CABINET


def test_allow_does_not_flow_upwards_or_sideways() -> None:
    subject = tree((FOLDER, "read", "allow"))
    assert subject.decide(CABINET, "read").allowed is False
    assert subject.decide(OTHER, "read").allowed is False


def test_explicit_deny_overrides_inherited_allow() -> None:
    subject = tree((CABINET, "read", "allow"), (FOLDER, "read", "deny"))
    assert subject.decide(FOLDER, "read").allowed is False
    assert subject.decide(SUBFOLDER, "read").allowed is False


def test_lower_allow_is_an_exception_to_inherited_deny() -> None:
    subject = tree((CABINET, "read", "deny"), (SUBFOLDER, "read", "allow"))
    assert subject.decide(SUBFOLDER, "read").allowed is True
    assert subject.decide(FOLDER, "read").allowed is False


def test_deny_beats_allow_at_the_same_level() -> None:
    entries = [
        AnchorEntry(FOLDER, USER, "read", "allow"),
        AnchorEntry(FOLDER, GROUP, "read", "deny"),
    ]
    assert AnchorTree(PARENTS, entries).decide(FOLDER, "read").allowed is False


def test_each_permission_resolves_independently() -> None:
    entries = [AnchorEntry(FOLDER, USER, "read", "allow")]
    subject = AnchorTree(PARENTS, entries)
    assert subject.decide(FOLDER, "read").allowed is True
    assert subject.decide(FOLDER, "download").allowed is False


def test_allowed_anchors_lists_exactly_the_readable_nodes() -> None:
    subject = tree((CABINET, "read", "allow"), (SUBFOLDER, "read", "deny"))
    assert subject.allowed_anchors("read") == {CABINET, FOLDER}


def test_a_cycle_in_bad_data_cannot_hang_or_grant_access() -> None:
    first, second = uuid.uuid4(), uuid.uuid4()
    subject = AnchorTree({first: second, second: first}, [])
    assert subject.decide(first, "read").allowed is False


@pytest.mark.parametrize("anchor", [uuid.uuid4()])
def test_unknown_anchor_is_denied(anchor: uuid.UUID) -> None:
    assert tree((TENANT, "read", "allow")).decide(anchor, "read").allowed is False
