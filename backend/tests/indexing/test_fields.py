"""Field definitions and values: what is accepted, and what is said when it is not."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

import pytest

from docuflow.modules.indexing.models import FieldChoice, IndexField
from docuflow.modules.indexing.service import (
    ChoiceInput,
    FieldInput,
    FieldView,
    check_definition,
    coerce,
    render_number,
    render_value,
)


def view(data_type: str, validation: dict[str, Any] | None = None, choices: tuple[str, ...] = ()):
    stored = IndexField(
        key="f",
        label="F",
        data_type=data_type,
        required=False,
        read_only=False,
        validation=validation or {},
    )
    return FieldView(stored, [FieldChoice(value=c, label=c.title(), position=0) for c in choices])


def definition(**overrides: Any) -> FieldInput:
    values: dict[str, Any] = {"key": "total", "label": "Total", "data_type": "number"}
    return FieldInput(**{**values, **overrides})


# Values


@pytest.mark.parametrize("empty", [None, "", "   ", "\n"])
def test_nothing_typed_is_empty_for_every_kind(empty: Any) -> None:
    for kind in ("text", "number", "date", "boolean"):
        assert coerce(view(kind), empty) is None
    assert coerce(view("choice", choices=("a",)), empty) is None


def test_text_is_trimmed_and_checked_against_length_and_pattern() -> None:
    rules = {"min_length": 3, "max_length": 8, "pattern": r"INV-\d+"}
    field = view("text", rules)
    assert coerce(field, "  INV-1042 ") == "INV-1042"
    with pytest.raises(ValueError, match="at least 3"):
        coerce(field, "IN")
    with pytest.raises(ValueError, match="at most 8"):
        coerce(field, "INV-123456")
    with pytest.raises(ValueError, match="expected format"):
        coerce(field, "ABC-1042")


def test_a_pattern_must_match_the_whole_value() -> None:
    field = view("text", {"pattern": r"\d{4}"})
    assert coerce(field, "2026") == "2026"
    with pytest.raises(ValueError, match="expected format"):
        coerce(field, "2026-11")


def test_a_pattern_is_never_run_on_a_huge_value() -> None:
    field = view("text", {"pattern": r"[a-z]+"})
    with pytest.raises(ValueError, match=r"\S"):
        coerce(field, "a" * 600)


def test_numbers_are_exact_and_ranged() -> None:
    field = view("number", {"min": 0, "max": "10000"})
    assert coerce(field, "1042.50") == Decimal("1042.5")
    assert coerce(field, 7) == Decimal(7)
    assert coerce(field, 0.1) == Decimal("0.1")  # not 0.1000000000000000055
    with pytest.raises(ValueError, match="0 or more"):
        coerce(field, "-1")
    with pytest.raises(ValueError, match="10000 or less"):
        coerce(field, "10000.01")


@pytest.mark.parametrize("bad", ["abc", "1,5", "NaN", "Infinity", "1e999999", True, [1], {"a": 1}])
def test_things_that_are_not_numbers_are_refused(bad: Any) -> None:
    with pytest.raises(ValueError, match=r"\S"):
        coerce(view("number"), bad)


def test_numbers_render_without_trailing_zeros() -> None:
    assert render_number(Decimal("1500.000000")) == "1500"
    assert render_number(Decimal("0.500000")) == "0.5"
    assert render_number(Decimal("1E+3")) == "1000"
    assert render_value("number", Decimal("12.340000")) == "12.34"
    assert render_value("date", date(2026, 11, 30)) == "2026-11-30"
    assert render_value("text", None) is None


def test_dates_are_iso_and_ranged() -> None:
    field = view("date", {"min": "2026-01-01", "max": "2026-12-31"})
    assert coerce(field, "2026-11-30") == date(2026, 11, 30)
    with pytest.raises(ValueError, match="year-month-day"):
        coerce(field, "30/11/2026")
    with pytest.raises(ValueError, match="2026-01-01 or later"):
        coerce(field, "2025-12-31")
    with pytest.raises(ValueError, match="2026-12-31 or earlier"):
        coerce(field, "2027-01-01")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(True, True), ("Yes", True), ("1", True), ("no", False), (False, False), ("OFF", False)],
)
def test_booleans_accept_the_usual_words(raw: Any, expected: bool) -> None:
    assert coerce(view("boolean"), raw) is expected


def test_a_boolean_must_be_a_yes_or_a_no() -> None:
    with pytest.raises(ValueError, match="yes or no"):
        coerce(view("boolean"), "maybe")


def test_a_choice_must_be_one_of_the_listed_values() -> None:
    field = view("choice", choices=("paid", "unpaid"))
    assert coerce(field, " paid ") == "paid"
    with pytest.raises(ValueError, match="listed"):
        coerce(field, "pending")


# Definitions


def test_a_good_definition_passes() -> None:
    check_definition(definition())
    check_definition(
        definition(
            key="status",
            data_type="choice",
            choices=(ChoiceInput("paid", "Paid"), ChoiceInput("unpaid", "Unpaid")),
            default_value="unpaid",
        )
    )
    check_definition(
        definition(
            key="ref", data_type="text", validation={"pattern": r"INV-\d+", "max_length": 20}
        )
    )


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"key": "Total"}, "lowercase"),
        ({"key": "1total"}, "lowercase"),
        ({"key": "a" * 41}, "40 characters"),
        ({"key": "to tal"}, "lowercase"),
        ({"label": "  "}, "label"),
        ({"data_type": "image"}, "text, number"),
        ({"validation": {"pattern": "x"}}, "does not apply"),
        ({"data_type": "text", "validation": {"pattern": "("}}, "not valid"),
        ({"data_type": "text", "validation": {"pattern": "(a+)+"}}, "repeats a repeat"),
        ({"data_type": "text", "validation": {"pattern": "x" * 201}}, "1 to 200"),
        ({"data_type": "text", "validation": {"min_length": 5, "max_length": 2}}, "min_length"),
        ({"validation": {"min": 10, "max": 1}}, "min cannot"),
        ({"validation": {"min": "ten"}}, "min:"),
        ({"data_type": "date", "validation": {"min": "yesterday"}}, "min:"),
        ({"data_type": "choice"}, "at least one choice"),
        (
            {
                "data_type": "choice",
                "choices": (ChoiceInput("a", "A"), ChoiceInput("a", "Again")),
            },
            "different values",
        ),
        ({"choices": (ChoiceInput("a", "A"),)}, "Only a choice"),
        ({"default_value": "lots"}, "default value is not valid"),
        ({"validation": {"max": 5}, "default_value": "9"}, "default value is not valid"),
    ],
)
def test_bad_definitions_say_what_is_wrong(overrides: dict[str, Any], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        check_definition(definition(**overrides))
