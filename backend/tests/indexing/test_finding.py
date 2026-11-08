"""Finding values and proposing types: rules, normalising, confidence and where on the page."""

from __future__ import annotations

from typing import Any

import pytest

from docuflow.modules.indexing.finding import (
    Found,
    Located,
    PageText,
    band,
    classification_confidence,
    classify,
    clean_number,
    find_values,
    locate,
    normalise_date,
    score,
    to_value_text,
)

INVOICE = (
    "ACME Supplies Ltd\n"
    "Invoice No: INV-1042\n"
    "Invoice date: 12 November 2026\n"
    "Due date: 30/11/2026\n"
    "Total due: RWF 1,042.50\n"
    "Paid: no\n"
)


def page(text: str = INVOICE, number: int = 1, **kwargs: Any) -> PageText:
    return PageText(page_id=f"page-{number}", number=number, text=text, **kwargs)


# Normalising


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1042.50", "1042.50"),
        ("RWF 1,042.50", "1042.50"),
        ("1.042,50 €", "1042.50"),
        ("$ 2,500", "2500"),
        ("1,5", "1.5"),
        ("12,345", "12345"),
        ("1'234'567.8", "1234567.8"),
        ("(25.00)", "-25.00"),
        ("-7", "-7"),
        ("1.234.567", "1234567"),
    ],
)
def test_numbers_are_read_the_way_people_write_them(raw: str, expected: str) -> None:
    assert clean_number(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2026-11-30", "2026-11-30"),
        ("30/11/2026", "2026-11-30"),
        ("30.11.26", "2026-11-30"),
        ("11/30/2026", "2026-11-30"),  # cannot be day-first, so month-first
        ("12 November 2026", "2026-11-12"),
        ("1st Nov 2026", "2026-11-01"),
        ("November 12, 2026", "2026-11-12"),
        ("12 novembre 2026", "2026-11-12"),
        ("31 février 2026", None),
        ("someday", None),
        ("2026-13-01", None),
    ],
)
def test_dates_become_iso_dates_or_nothing(raw: str, expected: str | None) -> None:
    assert normalise_date(raw) == expected


def test_value_text_for_each_kind() -> None:
    assert to_value_text("number", "RWF 1,042.50") == "1042.50"
    assert to_value_text("number", "abc") is None
    assert to_value_text("date", "12 Nov 2026") == "2026-11-12"
    assert to_value_text("boolean", "Yes") == "yes"
    assert to_value_text("choice", "OPEN", [("open", "Open"), ("closed", "Closed")]) == "open"
    assert to_value_text("choice", "pending", [("open", "Open")]) is None
    assert to_value_text("text", "  INV-1  ") == "INV-1"


# Finding


def test_a_label_finds_the_value_after_it_on_the_same_line() -> None:
    (found,) = find_values("text", {"labels": ["Invoice No"]}, [page()])
    assert (found.raw, found.method, found.base) == ("INV-1042", "label", 0.85)
    assert INVOICE[found.start : found.end] == "INV-1042"


def test_a_label_is_case_and_spacing_tolerant_and_tries_each_label() -> None:
    text = "invoice   NUMBER ## A-77\n"
    (found,) = find_values("text", {"labels": ["Invoice No", "invoice number"]}, [page(text)])
    assert found.raw == "A-77"


def test_a_value_on_the_next_line_is_found() -> None:
    text = "Invoice No\nINV-2000\nSupplier\nACME\n"
    (found,) = find_values("text", {"labels": ["Invoice No"]}, [page(text)])
    assert found.raw == "INV-2000"
    assert text[found.start : found.end] == "INV-2000"


def test_numbers_and_dates_are_picked_out_of_the_line() -> None:
    (total,) = find_values("number", {"labels": ["Total due"]}, [page()])
    assert total.raw == "RWF 1,042.50"
    (due,) = find_values("date", {"labels": ["Due date"]}, [page()])
    assert due.raw == "30/11/2026"
    (issued,) = find_values("date", {"labels": ["Invoice date"]}, [page()])
    assert issued.raw == "12 November 2026"
    (paid,) = find_values("boolean", {"labels": ["Paid"]}, [page()])
    assert paid.raw == "no"


def test_a_choice_is_found_by_its_label_or_value() -> None:
    text = "Status: Closed since Friday\n"
    choices = [("open", "Open"), ("closed", "Closed")]
    (found,) = find_values("choice", {"labels": ["Status"]}, [page(text)], choices)
    assert found.raw == "Closed"


def test_a_pattern_finds_a_value_with_no_label() -> None:
    text = "Ref abc\nplease quote INV-9001 on payment\n"
    (found,) = find_values("text", {"pattern": r"INV-\d{4}"}, [page(text)])
    assert (found.raw, found.method, found.base) == ("INV-9001", "pattern", 0.8)


def test_a_pattern_with_a_group_returns_the_group() -> None:
    (found,) = find_values(
        "text", {"pattern": r"Customer ID\s*(\d+)"}, [page("Customer ID  4471\n")]
    )
    assert found.raw == "4471"


def test_a_label_value_that_also_fits_the_pattern_is_the_most_trusted() -> None:
    rules = {"labels": ["Invoice No"], "pattern": r"INV-\d{4}"}
    (found,) = find_values("text", rules, [page()])
    assert (found.raw, found.method, found.base) == ("INV-1042", "label+pattern", 0.95)


def test_each_page_can_yield_a_value_in_page_order() -> None:
    pages = [page("Nothing here\n", 1), page("Total: 10\n", 2), page("Total: 20\n", 3)]
    found = find_values("number", {"labels": ["Total"]}, pages)
    assert [(item.page.number, item.raw) for item in found] == [(2, "10"), (3, "20")]


@pytest.mark.parametrize("rules", [{}, {"labels": []}, {"labels": ["Nonexistent"]}])
def test_no_rule_or_no_match_finds_nothing(rules: dict[str, Any]) -> None:
    assert find_values("text", rules, [page()]) == []


def test_a_broken_pattern_finds_nothing_instead_of_failing() -> None:
    assert find_values("text", {"pattern": "("}, [page()]) == []


# Where, and how sure


def test_the_words_a_value_covers_are_located_with_their_confidence() -> None:
    words = [
        {"t": "Total", "x": 10, "y": 10, "w": 50, "h": 20, "c": 0.99},
        {"t": "RWF", "x": 70, "y": 10, "w": 40, "h": 20, "c": 0.9},
        {"t": "1,042.50", "x": 115, "y": 10, "w": 80, "h": 20, "c": 0.7},
    ]
    source = page("Total RWF 1,042.50\n", words=words, width=900, height=400, confidence=0.86)
    (found,) = find_values("number", {"labels": ["Total"]}, [source])
    located = locate(found)
    assert [box["x"] for box in located.boxes] == [70, 115]
    assert located.confidence == pytest.approx(0.8)


def test_a_page_with_no_words_has_no_boxes() -> None:
    (found,) = find_values("text", {"labels": ["Invoice No"]}, [page()])
    assert locate(found) == Located()


def test_confidence_falls_with_doubtful_reading_and_with_a_value_that_does_not_check_out() -> None:
    source = page()
    found = Found("INV-1", 0, 5, source, "label", 0.85)
    assert score(found, Located(), True) == 0.85
    assert score(found, Located(None, 0.5), True) == pytest.approx(0.85 * 0.7, abs=0.001)
    assert score(found, Located(), False) == pytest.approx(0.425)
    weak = Found("INV-1", 0, 5, page(confidence=0.4), "label", 0.85)
    assert score(weak, Located(), True) == pytest.approx(0.85 * 0.64, abs=0.001)


def test_confidence_bands() -> None:
    assert band(0.95, 0.9, 0.6) == "high"
    assert band(0.9, 0.9, 0.6) == "high"
    assert band(0.7, 0.9, 0.6) == "review"
    assert band(0.59, 0.9, 0.6) == "low"


# Proposing a type


def test_a_type_is_proposed_by_the_share_of_its_keywords_found() -> None:
    types = [
        ("inv", "Invoice", ["invoice", "due date", "total due", "vat"]),
        ("hr", "Employee file", ["employee", "contract", "salary"]),
        ("none", "No keywords", []),
    ]
    ranked = classify(INVOICE, types)
    assert [item.name for item in ranked] == ["Invoice"]
    assert ranked[0].score == 0.75
    assert ranked[0].matched == ["invoice", "due date", "total due"]


def test_keywords_match_whole_words_only() -> None:
    assert classify("The invoiced amount", [("a", "A", ["invoice"])]) == []
    assert classify("An INVOICE arrived", [("a", "A", ["invoice"])])[0].score == 1.0


def test_a_close_second_lowers_confidence() -> None:
    text = "invoice contract"
    ranked = classify(text, [("a", "A", ["invoice", "x"]), ("b", "B", ["contract", "y"])])
    assert [item.score for item in ranked] == [0.5, 0.5]
    assert classification_confidence(ranked) == pytest.approx(0.35)
    assert classification_confidence(ranked[:1]) == 0.5
    assert classification_confidence([]) == 0.0
