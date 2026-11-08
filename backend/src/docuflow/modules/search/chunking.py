"""Cutting a document's pages into passages that can be found and cited (F18).

A passage never crosses a page, so a citation can always say which page it came from. A long page
is cut at word boundaries into overlapping pieces, so a sentence that straddles a cut is whole in
at least one of them.
"""

from __future__ import annotations

from dataclasses import dataclass

MAX_CHUNK = 1000
OVERLAP = 150
MAX_CHUNKS = 2000


@dataclass(frozen=True)
class Passage:
    page: int
    ordinal: int
    text: str


def split_page(text: str) -> list[str]:
    """One page as passages of at most MAX_CHUNK characters."""
    clean = " ".join(text.split())
    if not clean:
        return []
    if len(clean) <= MAX_CHUNK:
        return [clean]
    pieces: list[str] = []
    start = 0
    while start < len(clean):
        end = min(len(clean), start + MAX_CHUNK)
        if end < len(clean):
            cut = clean.rfind(" ", start + MAX_CHUNK // 2, end)
            end = cut if cut > start else end
        pieces.append(clean[start:end].strip())
        if end >= len(clean):
            break
        # Step back to a word boundary so the next piece begins with a whole word.
        back = clean.rfind(" ", start, max(start + 1, end - OVERLAP))
        start = back + 1 if back > start else end
    return [p for p in pieces if p]


def split_pages(pages: list[tuple[int, str]]) -> list[Passage]:
    """Passages for every page, in order, up to a sane limit."""
    passages: list[Passage] = []
    for number, text in pages:
        for piece in split_page(text):
            if len(passages) >= MAX_CHUNKS:
                return passages
            passages.append(Passage(number, len(passages), piece))
    return passages
