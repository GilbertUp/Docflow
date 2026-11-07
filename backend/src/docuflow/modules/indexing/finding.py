"""Finding field values in the text of a document, and proposing its type (F07).

This is rule based and explainable: a field names the labels that sit next to its value and
optionally a pattern, and a type names the words that give it away. Every value found keeps the
page it came from, the characters it covers, and the words' boxes when the page was recognised,
so a person can see exactly why it was proposed. Nothing here guesses beyond its rules.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

MAX_PAGE_TEXT = 200_000
MAX_LABELS = 10
MAX_LABEL = 60
MAX_KEYWORDS = 20
LABEL_GAP = r"[ \t]*[:#.\-\u2013\u2014=]*[ \t]*"
LABEL_WEIGHT = 0.85
PATTERN_WEIGHT = 0.8
BOTH_WEIGHT = 0.95
INVALID_PENALTY = 0.5

MONTHS = {
    name: number
    for number, names in enumerate(
        [
            ("january", "jan", "janvier"),
            ("february", "feb", "février", "fevrier"),
            ("march", "mar", "mars"),
            ("april", "apr", "avril"),
            ("may", "mai"),
            ("june", "jun", "juin"),
            ("july", "jul", "juillet"),
            ("august", "aug", "août", "aout"),
            ("september", "sep", "sept", "septembre"),
            ("october", "oct", "octobre"),
            ("november", "nov", "novembre"),
            ("december", "dec", "décembre", "decembre"),
        ],
        start=1,
    )
    for name in names
}
MONTH_WORDS = "|".join(sorted(MONTHS, key=len, reverse=True))
DATE_PATTERNS = (
    re.compile(r"\b\d{4}-\d{1,2}-\d{1,2}\b"),
    re.compile(r"\b\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4}\b"),
    re.compile(rf"\b\d{{1,2}}(?:st|nd|rd|th)?\s+(?:{MONTH_WORDS})\.?,?\s+\d{{4}}\b", re.IGNORECASE),
    re.compile(rf"\b(?:{MONTH_WORDS})\.?\s+\d{{1,2}}(?:st|nd|rd|th)?,?\s+\d{{4}}\b", re.IGNORECASE),
)
NUMBER = re.compile(r"[-+(]?\s*(?:[A-Za-z]{3}\s*|[$€£]\s*)?\d[\d.,' ]*\d|[-+(]?\s*\d")
BOOLEAN = re.compile(r"\b(yes|no|true|false)\b", re.IGNORECASE)


@dataclass(frozen=True)
class PageText:
    """One page as the finder sees it: its text, and the recognised words with their boxes."""

    page_id: Any
    number: int
    text: str
    words: Sequence[dict[str, Any]] = ()
    width: int = 0
    height: int = 0
    confidence: float | None = None


@dataclass(frozen=True)
class Found:
    raw: str
    start: int
    end: int
    page: PageText
    method: str  # "label", "pattern" or "label+pattern"
    base: float


@dataclass(frozen=True)
class Located:
    boxes: list[dict[str, int]] = field(default_factory=list)
    confidence: float | None = None


# Turning what was found into a value


def clean_number(raw: str) -> str:
    """A plain decimal from text such as "RWF 1,042.50", "1.042,50 €" or "(25.00)"."""
    text = raw.strip()
    negative = text.startswith("(") or text.startswith("-")
    digits = re.sub(r"[^\d.,']", "", text).replace("'", "")
    if "," in digits and "." in digits:
        # The separator that comes last is the decimal point.
        decimal = "," if digits.rfind(",") > digits.rfind(".") else "."
        thousands = "." if decimal == "," else ","
        digits = digits.replace(thousands, "").replace(decimal, ".")
    elif "," in digits:
        parts = digits.split(",")
        digits = ".".join(parts) if len(parts) == 2 and len(parts[1]) != 3 else "".join(parts)
    elif digits.count(".") > 1:
        digits = digits.replace(".", "")
    return ("-" if negative else "") + digits


def normalise_date(raw: str) -> str | None:
    """An ISO date from a date as people write it, or None when it cannot be read."""
    text = raw.strip().rstrip(".")
    iso = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", text)
    if iso:
        year, month, day = (int(part) for part in iso.groups())
        return _date(year, month, day)
    numeric = re.fullmatch(r"(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{2,4})", text)
    if numeric:
        first, second, year_text = numeric.groups()
        year = int(year_text) + (2000 if len(year_text) == 2 else 0)
        # Day first, as in most of the world, unless that cannot be a month and the other can.
        day, month = int(first), int(second)
        if month > 12 >= day:
            day, month = month, day
        return _date(year, month, day)
    named = re.fullmatch(
        rf"(\d{{1,2}})(?:st|nd|rd|th)?\s+({MONTH_WORDS})\.?,?\s+(\d{{4}})", text, re.IGNORECASE
    )
    if named:
        return _date(int(named.group(3)), MONTHS[named.group(2).lower()], int(named.group(1)))
    american = re.fullmatch(
        rf"({MONTH_WORDS})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{4}})", text, re.IGNORECASE
    )
    if american:
        return _date(
            int(american.group(3)), MONTHS[american.group(1).lower()], int(american.group(2))
        )
    return None


def _date(year: int, month: int, day: int) -> str | None:
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None


def to_value_text(data_type: str, raw: str, choices: Sequence[tuple[str, str]] = ()) -> str | None:
    """The text a field's value check will accept for what was found, or None."""
    text = raw.strip()
    if data_type == "number":
        cleaned = clean_number(text)
        try:
            Decimal(cleaned)
        except InvalidOperation:
            return None
        return cleaned
    if data_type == "date":
        return normalise_date(text)
    if data_type == "boolean":
        return text.lower()
    if data_type == "choice":
        lowered = text.lower()
        for value, label in choices:
            if lowered in (value.lower(), label.lower()):
                return value
        return None
    return text


# Looking for values


def _label_regex(label: str) -> re.Pattern[str]:
    words = [re.escape(word) for word in label.split()]
    return re.compile(r"(?<![\w])" + r"\s+".join(words) + r"(?![\w])", re.IGNORECASE)


def _capture(data_type: str, text: str, choices: Sequence[tuple[str, str]]) -> re.Match[str] | None:
    """The first thing in text that looks like a value of this kind."""
    if data_type == "number":
        return NUMBER.search(text)
    if data_type == "date":
        hits = [match for pattern in DATE_PATTERNS if (match := pattern.search(text))]
        return min(hits, key=lambda match: match.start()) if hits else None
    if data_type == "boolean":
        return BOOLEAN.search(text)
    if data_type == "choice":
        for value, label in choices:
            for candidate in (label, value):
                match = re.search(rf"(?<!\w){re.escape(candidate)}(?!\w)", text, re.IGNORECASE)
                if match:
                    return match
        return None
    stripped = re.split(r"\s{3,}|\t", text.strip(), maxsplit=1)[0].strip()
    if not stripped:
        return None
    start = text.index(stripped[0])
    return re.compile(re.escape(stripped)).search(text, start)


def _after_label(
    page: PageText, label: str, data_type: str, choices: Sequence[tuple[str, str]]
) -> Found | None:
    text = page.text[:MAX_PAGE_TEXT]
    for hit in _label_regex(label).finditer(text):
        gap = re.compile(LABEL_GAP).match(text, hit.end())
        position = gap.end() if gap else hit.end()
        line_end = text.find("\n", position)
        line_end = len(text) if line_end == -1 else line_end
        same_line = text[position:line_end]
        match = _capture(data_type, same_line, choices)
        offset = position
        if match is None:
            # The value may sit on the next line, as it does in many tables and forms.
            next_start = line_end + 1
            while next_start < len(text) and text[next_start] == "\n":
                next_start += 1
            next_end = text.find("\n", next_start)
            next_end = len(text) if next_end == -1 else next_end
            match = _capture(data_type, text[next_start:next_end], choices)
            offset = next_start
        if match is not None and match.group(0).strip():
            raw = match.group(0).strip()
            start = offset + match.start() + (len(match.group(0)) - len(match.group(0).lstrip()))
            return Found(raw, start, start + len(raw), page, "label", LABEL_WEIGHT)
    return None


def _by_pattern(page: PageText, pattern: str) -> Found | None:
    try:
        match = re.search(pattern, page.text[:MAX_PAGE_TEXT])
    except re.error:
        return None
    if match is None:
        return None
    group = 1 if match.re.groups >= 1 and match.group(1) else 0
    raw = match.group(group).strip()
    if not raw:
        return None
    return Found(raw, match.start(group), match.end(group), page, "pattern", PATTERN_WEIGHT)


def find_values(
    data_type: str,
    extraction: dict[str, Any],
    pages: Sequence[PageText],
    choices: Sequence[tuple[str, str]] = (),
) -> list[Found]:
    """Everything the field's rules find, in page order. Empty when it has no rules."""
    labels = [label for label in extraction.get("labels", []) if isinstance(label, str)]
    pattern = extraction.get("pattern")
    found: list[Found] = []
    for page in pages:
        by_label = next(
            (hit for label in labels if (hit := _after_label(page, label, data_type, choices))),
            None,
        )
        by_pattern = _by_pattern(page, pattern) if isinstance(pattern, str) and pattern else None
        if by_label and by_pattern and re.fullmatch(pattern, by_label.raw):  # type: ignore[arg-type]
            found.append(
                Found(
                    by_label.raw, by_label.start, by_label.end, page, "label+pattern", BOTH_WEIGHT
                )
            )
        elif by_label and not (isinstance(pattern, str) and pattern):
            found.append(by_label)
        elif by_pattern:
            found.append(by_pattern)
        elif by_label:
            found.append(by_label)
    return found


# Where on the page


def locate(found: Found) -> Located:
    """The recognised words a value covers, and how sure the recogniser was about them."""
    words = list(found.page.words)
    if not words:
        return Located()
    tokens = [token for token in re.split(r"\s+", found.raw.strip()) if token]
    if not tokens:
        return Located()
    for index in range(len(words) - len(tokens) + 1):
        window = words[index : index + len(tokens)]
        if all(
            str(word.get("t", "")).strip() == token
            for word, token in zip(window, tokens, strict=True)
        ):
            boxes = [
                {"x": int(w["x"]), "y": int(w["y"]), "w": int(w["w"]), "h": int(w["h"])}
                for w in window
            ]
            scores = [float(w.get("c", 1.0)) for w in window]
            return Located(boxes, round(sum(scores) / len(scores), 3))
    return Located()


def score(found: Found, located: Located, valid: bool) -> float:
    """Confidence from the rule that matched, the recogniser's doubt, and whether it checks out."""
    confidence = found.base
    reading = located.confidence if located.confidence is not None else found.page.confidence
    if reading is not None:
        confidence *= 0.4 + 0.6 * min(1.0, reading)
    if not valid:
        confidence *= INVALID_PENALTY
    return round(max(0.0, min(1.0, confidence)), 3)


def band(confidence: float, auto_accept: float, review: float) -> str:
    """ "high" is filled in at once, "review" waits for a person, and "low" is a weak guess."""
    if confidence >= auto_accept:
        return "high"
    return "review" if confidence >= review else "low"


# Proposing a type


@dataclass(frozen=True)
class TypeScore:
    type_id: Any
    name: str
    score: float
    matched: list[str]


def classify(text: str, types: Sequence[tuple[Any, str, Sequence[str]]]) -> list[TypeScore]:
    """Types ranked by how many of their keywords the text contains. Types with none match drop."""
    lowered = text[: MAX_PAGE_TEXT * 5].lower()
    ranked: list[TypeScore] = []
    for type_id, name, keywords in types:
        usable = [word.strip() for word in keywords if isinstance(word, str) and word.strip()]
        if not usable:
            continue
        matched = [
            word
            for word in usable
            if re.search(rf"(?<!\w){re.escape(word.lower())}(?!\w)", lowered)
        ]
        if matched:
            ranked.append(TypeScore(type_id, name, round(len(matched) / len(usable), 3), matched))
    return sorted(ranked, key=lambda item: (-item.score, item.name.lower()))


def classification_confidence(ranked: Sequence[TypeScore]) -> float:
    """How sure to be of the best type: its score, less a share when another type is close."""
    if not ranked:
        return 0.0
    best = ranked[0].score
    second = ranked[1].score if len(ranked) > 1 else 0.0
    return round(best * (1 - 0.3 * (second / best if best else 0)), 3)
