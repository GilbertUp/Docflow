"""Synthetic scanned pages and files for the compression tests. No real documents are used."""

from __future__ import annotations

import io
import random
import zlib
from pathlib import Path

from PIL import Image, ImageDraw

from docuflow.modules.compression import engine

DPI = 150
# Chosen so that, at DPI, a page is a whole number of points and renders to exactly these pixels.
SIZE = (600, 850)


def text_page(seed: int = 1) -> Image.Image:
    """Black marks on white, as a scan of printed text looks after cleaning."""
    rng = random.Random(seed)  # noqa: S311  (synthetic test pages, not secrets)
    image = Image.new("RGB", SIZE, "white")
    draw = ImageDraw.Draw(image)
    for row in range(40, SIZE[1] - 40, 22):
        x = 40
        while x < SIZE[0] - 60:
            word = rng.randint(14, 70)
            draw.rectangle([x, row, x + word, row + 10], fill="black")
            x += word + rng.randint(8, 16)
    return image


def photo_page(seed: int = 2) -> Image.Image:
    """Smooth colour with a little grain, like a photograph."""
    rng = random.Random(seed)  # noqa: S311  (synthetic test pages, not secrets)
    image = Image.new("RGB", SIZE)
    pixels = image.load()
    assert pixels is not None
    for y in range(SIZE[1]):
        for x in range(SIZE[0]):
            base = (x * 255 // SIZE[0], y * 255 // SIZE[1], (x + y) * 255 // (SIZE[0] + SIZE[1]))
            pixels[x, y] = tuple(min(255, max(0, c + rng.randint(-3, 3))) for c in base)
    return image


def noise_page(seed: int = 3) -> Image.Image:
    """Random pixels. Nothing compresses this without visible loss."""
    rng = random.Random(seed)  # noqa: S311  (synthetic test pages, not secrets)
    data = bytes(rng.randrange(256) for _ in range(SIZE[0] * SIZE[1] * 3))
    return Image.frombytes("RGB", SIZE, data)


def blank_page() -> Image.Image:
    return Image.new("RGB", SIZE, "white")


def scanned_pdf(pages: list[Image.Image], quality: int = 95) -> bytes:
    """A PDF whose pages are high-quality pictures, like a scan straight from the scanner."""
    sizes = [(p.width * 72 / DPI, p.height * 72 / DPI) for p in pages]
    return engine.write_pdf([engine._page_from_jpeg(p, quality)[0] for p in pages], sizes)


def grey_flate_pdf(pages: list[Image.Image]) -> bytes:
    """A black and white scan stored as 8-bit grey with lossless compression, which is common."""
    sizes = [(p.width * 72 / DPI, p.height * 72 / DPI) for p in pages]
    streams = [
        engine._Page(
            p.width,
            p.height,
            zlib.compress(p.convert("L").tobytes(), 6),
            "/FlateDecode",
            "/DeviceGray",
            8,
        )
        for p in pages
    ]
    return engine.write_pdf(streams, sizes)


def png_bytes(image: Image.Image) -> bytes:
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


def jpeg_bytes(image: Image.Image, quality: int = 95) -> bytes:
    out = io.BytesIO()
    image.save(out, "JPEG", quality=quality)
    return out.getvalue()


def write(tmp_path: Path, data: bytes, name: str = "source") -> Path:
    path = tmp_path / name
    path.write_bytes(data)
    return path
