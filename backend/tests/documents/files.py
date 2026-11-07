"""Small real files for text-extraction tests, made without any test data on disk."""

from __future__ import annotations

import io
from collections.abc import Sequence
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from docuflow.modules.documents.ocr import OcrPage, OcrUnavailable, Word


def text_pdf(pages: Sequence[str]) -> bytes:
    """A valid PDF whose pages carry real text, one string per page."""
    objects: list[bytes] = []

    def add(body: str) -> int:
        objects.append(body.encode("latin-1"))
        return len(objects)

    catalog = add("<< /Type /Catalog /Pages 2 0 R >>")
    assert catalog == 1
    add("")  # the page tree, filled in below
    font = add("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    kids: list[int] = []
    for text in pages:
        escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        stream = f"BT /F1 12 Tf 72 720 Td ({escaped}) Tj ET"
        content = add(f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream")
        kids.append(
            add(
                "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                f"/Resources << /Font << /F1 {font} 0 R >> >> /Contents {content} 0 R >>"
            )
        )
    references = " ".join(f"{kid} 0 R" for kid in kids)
    objects[1] = f"<< /Type /Pages /Kids [{references}] /Count {len(kids)} >>".encode("latin-1")

    output = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(output))
        output += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    table = len(output)
    output += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for offset in offsets:
        output += f"{offset:010d} 00000 n \n".encode()
    output += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{table}\n%%EOF\n"
    ).encode()
    return bytes(output)


ACCENT_FONTS = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
    "/Library/Fonts/Arial.ttf",
    "C:/Windows/Fonts/arial.ttf",
)


def accented_font(size: int = 44) -> ImageFont.FreeTypeFont | None:
    """A font that really has letters such as é, ü and ñ. Pillow's built-in one draws other
    shapes for them, so a test that needs accents must not use it."""
    for path in ACCENT_FONTS:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return None


def drawn_page(
    text: str = "",
    size: tuple[int, int] = (900, 400),
    font: ImageFont.FreeTypeFont | None = None,
) -> Image.Image:
    """A white page with black text, large enough for OCR to read."""
    image = Image.new("RGB", size, "white")
    if text:
        draw = ImageDraw.Draw(image)
        font = font or ImageFont.load_default(size=44)
        draw.multiline_text((40, 40), text, fill="black", font=font, spacing=24)
    return image


def mark_page() -> Image.Image:
    """A page with some ink on it but nothing a person would call text."""
    image = Image.new("RGB", (600, 300), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((50, 50, 550, 250), outline="black", width=6)
    draw.line((50, 50, 550, 250), fill="black", width=6)
    return image


def pdf_of_images(images: Sequence[Image.Image]) -> bytes:
    """A scanned-style PDF: every page is a picture and there is no text in it."""
    buffer = io.BytesIO()
    images[0].save(buffer, "PDF", save_all=True, append_images=list(images[1:]), resolution=150)
    return buffer.getvalue()


def tiff_of_images(images: Sequence[Image.Image]) -> bytes:
    buffer = io.BytesIO()
    images[0].save(buffer, "TIFF", save_all=True, append_images=list(images[1:]))
    return buffer.getvalue()


def png_of(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


class FakeEngine:
    """Stands in for Tesseract. It reads nothing, and remembers what it was asked."""

    name = "fake"

    def __init__(
        self, installed: Sequence[str] = ("eng", "fra", "deu"), available: bool = True
    ) -> None:
        self.installed = list(installed)
        self.is_available = available
        self.calls: list[tuple[tuple[int, int], list[str]]] = []

    def available(self) -> bool:
        return self.is_available

    def version(self) -> str:
        return "fake 1.0"

    def languages(self) -> list[str]:
        return list(self.installed)

    def recognise(self, image: Image.Image, languages: Sequence[str]) -> OcrPage:
        if not self.is_available:
            raise OcrUnavailable("Reading scanned pages is not set up on this server.")
        self.calls.append((image.size, list(languages)))
        label = f"scanned page {len(self.calls)} read as {'+'.join(languages)}"
        return OcrPage(label, [Word("scanned", 1, 2, 3, 4, 0.9)], 0.9)
