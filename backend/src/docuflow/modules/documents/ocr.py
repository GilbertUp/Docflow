"""Optical character recognition behind one interface (ADR 0008).

Tesseract is the default engine and reads more than a hundred languages from installable
language packs. It is run as a program with no shell, one page image at a time, so a crash or a
hostile image can only ever take down that one run.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from PIL import Image

from docuflow.platform.errors import DomainError

LANGUAGE_CODE = re.compile(r"^[A-Za-z]{2,4}(_[A-Za-z0-9]{2,8})?$")
# Tesseract's own helper models. They detect orientation and script and are not languages.
NOT_LANGUAGES = {"osd", "equ"}
TSV_COLUMNS = 12
WORD_LEVEL = 5


class OcrUnavailable(DomainError):
    status = 503
    code = "ocr_unavailable"


class OcrFailed(Exception):
    """A page could not be read. The message is safe to show."""


@dataclass(frozen=True)
class Word:
    text: str
    x: int
    y: int
    w: int
    h: int
    confidence: float  # 0 to 1


@dataclass(frozen=True)
class OcrPage:
    text: str
    words: list[Word] = field(default_factory=list)
    confidence: float | None = None


class OcrEngine(Protocol):
    name: str

    def available(self) -> bool: ...

    def version(self) -> str: ...

    def languages(self) -> list[str]:
        """Language codes the engine can read right now."""
        ...

    def recognise(self, image: Image.Image, languages: Sequence[str]) -> OcrPage: ...


def parse_languages(raw: str | Sequence[str]) -> list[str]:
    """Language codes from "eng, fra+deu" or a list. Order is kept and repeats are dropped."""
    items = re.split(r"[,+\s]+", raw) if isinstance(raw, str) else list(raw)
    codes: list[str] = []
    for item in items:
        code = item.strip()
        if not code:
            continue
        if not LANGUAGE_CODE.match(code):
            raise DomainError(
                f"{code!r} is not a language code. Use codes such as eng, fra or deu."
            )
        if code not in codes:
            codes.append(code)
    return codes


def parse_tsv(tsv: str, width: int = 0, height: int = 0) -> OcrPage:
    """Turns Tesseract's TSV output into text, in reading order, and word boxes."""
    words: list[Word] = []
    pieces: list[str] = []
    previous: tuple[str, str, str] | None = None
    scores: list[float] = []
    for line in tsv.splitlines()[1:]:
        columns = line.split("\t")
        if len(columns) < TSV_COLUMNS or columns[0] != str(WORD_LEVEL):
            continue
        text = "\t".join(columns[11:]).strip()
        try:
            confidence = float(columns[10])
            left, top, w, h = (int(columns[index]) for index in (6, 7, 8, 9))
        except ValueError:
            continue
        if not text or confidence < 0:
            continue
        key = (columns[2], columns[3], columns[4])  # block, paragraph, line
        if previous is not None:
            if key[0] != previous[0]:
                pieces.append("\n\n")
            elif key[1] != previous[1]:
                pieces.append("\n\n")
            elif key[2] != previous[2]:
                pieces.append("\n")
            else:
                pieces.append(" ")
        pieces.append(text)
        previous = key
        scores.append(confidence / 100)
        words.append(Word(text, left, top, w, h, round(confidence / 100, 3)))
    mean = round(sum(scores) / len(scores), 3) if scores else None
    return OcrPage("".join(pieces), words, mean)


class TesseractEngine:
    name = "tesseract"

    def __init__(self, binary: str = "tesseract", timeout_seconds: int = 180) -> None:
        self._binary = binary
        self._timeout = timeout_seconds
        self._languages: list[str] | None = None
        self._version: str | None = None

    def _program(self) -> str | None:
        return shutil.which(self._binary)

    def available(self) -> bool:
        return self._program() is not None

    def _run(self, arguments: Sequence[str], timeout: int) -> subprocess.CompletedProcess[str]:
        program = self._program()
        if program is None:
            raise OcrUnavailable("Reading scanned pages is not set up on this server.")
        environment = {**os.environ, "OMP_THREAD_LIMIT": "1"}
        try:
            return subprocess.run(  # noqa: S603  # no shell, and every argument is validated
                [program, *arguments],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                check=False,
                env=environment,
            )
        except subprocess.TimeoutExpired as exc:
            raise OcrFailed("Reading this page took too long.") from exc

    def version(self) -> str:
        if self._version is None:
            output = self._run(["--version"], 30)
            first = (output.stdout or output.stderr).splitlines()
            self._version = first[0].strip() if first else "tesseract"
        return self._version

    def languages(self) -> list[str]:
        if self._languages is None:
            output = self._run(["--list-langs"], 30)
            names = [line.strip() for line in output.stdout.splitlines()[1:]]
            self._languages = sorted(name for name in names if name and name not in NOT_LANGUAGES)
        return list(self._languages)

    def recognise(self, image: Image.Image, languages: Sequence[str]) -> OcrPage:
        if not languages or not all(LANGUAGE_CODE.match(code) for code in languages):
            raise OcrFailed("No valid language was given.")
        with tempfile.TemporaryDirectory(prefix="docuflow-ocr-") as folder:
            path = Path(folder) / "page.png"
            image.convert("RGB").save(path, format="PNG")
            result = self._run(
                [str(path), "stdout", "-l", "+".join(languages), "--psm", "3", "tsv"],
                self._timeout,
            )
        if result.returncode != 0:
            raise OcrFailed(_reason(result.stderr))
        return parse_tsv(result.stdout, image.width, image.height)


def _reason(stderr: str) -> str:
    """A short, safe reason from Tesseract's error output."""
    if "Failed loading language" in stderr or "Error opening data file" in stderr:
        return "A language pack is missing on this server."
    return "The page could not be read."


def build_ocr_engine(settings: Any) -> TesseractEngine:
    return TesseractEngine(
        getattr(settings, "tesseract_binary", "tesseract"),
        getattr(settings, "ocr_page_timeout_seconds", 180),
    )
