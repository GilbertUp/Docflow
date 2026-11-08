"""Ids are RFC 9562 version 7 and always increase within a process."""

from __future__ import annotations

import threading
import uuid

import pytest

from docuflow.platform import ids


def test_ids_are_valid_version_7_uuids() -> None:
    value = ids.uuid7()
    assert value.version == 7
    assert value.variant == uuid.RFC_4122


def test_ids_made_in_a_row_sort_in_the_order_they_were_made() -> None:
    made = [ids.uuid7() for _ in range(20_000)]
    assert made == sorted(made)
    assert len(set(made)) == len(made)


def test_ids_stay_in_order_when_the_clock_does_not_move(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ids.time, "time_ns", lambda: 1_700_000_000_000_000_000)
    made = [ids.uuid7() for _ in range(5_000)]  # more than one counter's worth in one millisecond
    assert made == sorted(made)
    assert len(set(made)) == len(made)


def test_ids_never_go_backwards_when_the_clock_steps_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ids.time, "time_ns", lambda: 1_700_000_005_000_000_000)
    first = ids.uuid7()
    monkeypatch.setattr(ids.time, "time_ns", lambda: 1_700_000_000_000_000_000)
    assert ids.uuid7() > first


def test_threads_never_make_the_same_id() -> None:
    found: list[uuid.UUID] = []

    def work() -> None:
        found.extend(ids.uuid7() for _ in range(5_000))

    threads = [threading.Thread(target=work) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(set(found)) == len(found) == 40_000
