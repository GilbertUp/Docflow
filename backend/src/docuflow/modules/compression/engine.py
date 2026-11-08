"""Making a smaller copy of a scanned document, and proving whether it is good enough (F17).

This module knows nothing about the database. It takes a file and a profile and returns a
result, so every decision in it can be tested on its own.

Rules that hold throughout (ADR 0014 and ADR 0032):
- The original is never touched. The result is a separate copy.
- Each page is looked at and given the safest treatment that works: blank pages become almost
  nothing, line art and text scans become two-colour images with no loss of shape, and
  photographic pages are compressed by the smallest amount that still passes the check.
- Lossy two-colour coding that swaps one letter shape for another (JBIG2 symbol matching) is never
  used, because it can silently change characters.
- A page is judged on how it looks after the copy is rendered again, not on what we meant to
  write. The worst page decides, so a good average cannot hide one unreadable page.
- A copy that is not clearly smaller is reported as no benefit, and a copy that fails the check is
  kept with its numbers but is never recommended.
- A file with real text or vector drawing on its pages is left alone: turning it into pictures
  would lose the text, and this slice has no engine to shrink it safely.
"""

from __future__ import annotations

import io
import time
import zlib
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pypdfium2 as pdfium
from PIL import Image, ImageOps, UnidentifiedImageError

BLANK_SHARE = 0.995
LINE_ART_SHARE = 0.90
WHITE_FROM = 245
DARK_TO = 60
DISPLAY_REDUCE = 4
DISPLAY_MIN_SSIM = 0.85
DISPLAY_SIZE_SLACK = 2
LIGHT_FROM = 195
MAX_PAGES = 200
MAX_PIXELS = 40_000_000
PDF_TYPES = {"application/pdf"}
IMAGE_TYPES = {"image/png", "image/jpeg", "image/tiff", "image/webp", "image/gif"}
SUPPORTED = PDF_TYPES | IMAGE_TYPES
MIN_NATIVE_CHARACTERS = 25


@dataclass(frozen=True)
class Profile:
    """A named set of choices. A change to any value is a new version."""

    name: str = "standard"
    version: int = 1
    dpi: int = 150
    # A page must look this much like the original, by structural similarity (1 is identical).
    text_ssim: float = 0.97
    photo_ssim: float = 0.92
    # JPEG qualities to try for a photographic page, best first. The first that passes is used.
    jpeg_ladder: tuple[int, ...] = (80, 65, 50, 40)
    # The copy must be at least this much smaller than the original to be worth keeping.
    min_saving: float = 0.05

    def as_params(self) -> dict[str, Any]:
        return {
            "profile": self.name,
            "profile_version": self.version,
            "dpi": self.dpi,
            "text_ssim": self.text_ssim,
            "photo_ssim": self.photo_ssim,
            "jpeg_ladder": list(self.jpeg_ladder),
            "min_saving": self.min_saving,
        }


@dataclass
class PageResult:
    number: int
    kind: str  # blank, line_art or photographic
    treatment: str  # for example bilevel or jpeg-65
    ssim: float = 0.0
    psnr: float = 0.0
    threshold: float = 0.0
    passed: bool = False
    # How much the finished page, drawn again by a PDF reader, looks like the original.
    display_ssim: float | None = None


@dataclass
class Result:
    # accepted, rejected, no_benefit or unsupported
    status: str
    reason: str | None = None
    data: bytes | None = None
    mime: str | None = None
    pages: list[PageResult] = field(default_factory=list)
    source_size: int = 0
    seconds: float = 0.0

    @property
    def derivative_size(self) -> int:
        return len(self.data) if self.data else 0

    def metrics(self) -> dict[str, Any]:
        ssims = [p.ssim for p in self.pages]
        psnrs = [p.psnr for p in self.pages]
        smaller = self.derivative_size
        return {
            "pages": len(self.pages),
            "worst_ssim": round(min(ssims), 4) if ssims else None,
            "mean_ssim": round(sum(ssims) / len(ssims), 4) if ssims else None,
            "worst_psnr": round(min(psnrs), 2) if psnrs else None,
            "source_size": self.source_size,
            "derivative_size": smaller,
            "ratio": round(smaller / self.source_size, 4) if self.source_size and smaller else None,
            "reduction": round(1 - smaller / self.source_size, 4)
            if self.source_size and smaller
            else None,
            "seconds": round(self.seconds, 2),
            "per_page": [
                {
                    "page": p.number,
                    "kind": p.kind,
                    "treatment": p.treatment,
                    "ssim": round(p.ssim, 4),
                    "psnr": round(p.psnr, 2),
                    "threshold": p.threshold,
                    "passed": p.passed,
                    "display_ssim": None if p.display_ssim is None else round(p.display_ssim, 4),
                }
                for p in self.pages
            ],
        }


# Measuring


def _gray(image: Image.Image) -> np.ndarray:
    return np.asarray(image.convert("L"), dtype=np.float64)


def _box_sum(values: np.ndarray, size: int) -> np.ndarray:
    """The sum over every size by size window, using running totals."""
    padded = np.pad(values, ((1, 0), (1, 0)))
    total = padded.cumsum(axis=0).cumsum(axis=1)
    summed: np.ndarray = (
        total[size:, size:] - total[:-size, size:] - total[size:, :-size] + total[:-size, :-size]
    )
    return summed


def ssim(first: Image.Image, second: Image.Image, window: int = 7) -> float:
    """Structural similarity of two images of the same size, from 0 to 1."""
    a, b = _gray(first), _gray(second)
    if a.shape != b.shape:
        raise ValueError("The images must be the same size.")
    c1, c2 = (0.01 * 255) ** 2, (0.03 * 255) ** 2
    height, width = a.shape
    size = min(window, height, width)
    area = size * size
    mean_a, mean_b = _box_sum(a, size) / area, _box_sum(b, size) / area
    var_a = _box_sum(a * a, size) / area - mean_a**2
    var_b = _box_sum(b * b, size) / area - mean_b**2
    covariance = _box_sum(a * b, size) / area - mean_a * mean_b
    score = ((2 * mean_a * mean_b + c1) * (2 * covariance + c2)) / (
        (mean_a**2 + mean_b**2 + c1) * (var_a + var_b + c2)
    )
    return float(np.clip(score.mean(), 0.0, 1.0))


def psnr(first: Image.Image, second: Image.Image) -> float:
    """Peak signal to noise ratio in decibels. Identical images give 99."""
    a, b = _gray(first), _gray(second)
    error = float(np.mean((a - b) ** 2))
    if error == 0:
        return 99.0
    return min(99.0, float(10 * np.log10(255.0**2 / error)))


# Looking at a page


def classify(image: Image.Image) -> str:
    """blank, line_art (text and drawings) or photographic."""
    histogram = np.asarray(image.convert("L").histogram(), dtype=np.float64)
    total = histogram.sum()
    if total == 0:
        return "blank"
    if histogram[WHITE_FROM:].sum() / total >= BLANK_SHARE:
        return "blank"
    extremes = histogram[:DARK_TO].sum() + histogram[LIGHT_FROM:].sum()
    return "line_art" if extremes / total >= LINE_ART_SHARE else "photographic"


def _is_grey(image: Image.Image) -> bool:
    """True when colour adds nothing, so a grey image will do."""
    if image.mode in ("L", "1"):
        return True
    rgb = np.asarray(image.convert("RGB").resize((64, 64)), dtype=np.int16)
    spread = rgb.max(axis=2) - rgb.min(axis=2)
    return bool(spread.mean() < 6)


# Writing the copy


def _jpeg(image: Image.Image, quality: int) -> tuple[bytes, bool]:
    grey = _is_grey(image)
    out = io.BytesIO()
    (image.convert("L") if grey else image.convert("RGB")).save(
        out, "JPEG", quality=quality, optimize=True
    )
    return out.getvalue(), grey


def _bilevel(image: Image.Image) -> Image.Image:
    return image.convert("L").point(lambda v: 255 if v >= 128 else 0).convert("1")


def _packed_bits(image: Image.Image) -> bytes:
    """A two-colour image as the packed bits a PDF expects, white as 1."""
    return image.convert("1").tobytes()


@dataclass
class _Page:
    width: int
    height: int
    # (stream bytes, filter, colour space, bits per component)
    stream: bytes
    filter: str
    colour: str
    bits: int


def _page_from_jpeg(image: Image.Image, quality: int) -> tuple[_Page, bytes]:
    data, grey = _jpeg(image, quality)
    return (
        _Page(
            image.width,
            image.height,
            data,
            "/DCTDecode",
            "/DeviceGray" if grey else "/DeviceRGB",
            8,
        ),
        data,
    )


def _page_from_bilevel(image: Image.Image) -> _Page:
    packed = zlib.compress(_packed_bits(image), 9)
    return _Page(image.width, image.height, packed, "/FlateDecode", "/DeviceGray", 1)


def write_pdf(pages: list[_Page], sizes: list[tuple[float, float]]) -> bytes:
    """A PDF that shows each page's image on a page of the given size in points.

    The size comes from the original page, not from the picture's pixels, so that both render to
    exactly the same number of pixels and can be compared without resizing either.
    """
    objects: list[bytes] = []

    def add(body: bytes) -> int:
        objects.append(body)
        return len(objects)

    add(b"<< /Type /Catalog /Pages 2 0 R >>")
    add(b"")  # the page tree, filled in below
    kids: list[int] = []
    for number, page in enumerate(pages):
        width_pt, height_pt = sizes[number]
        image = add(
            (
                f"<< /Type /XObject /Subtype /Image /Width {page.width} /Height {page.height} "
                f"/ColorSpace {page.colour} /BitsPerComponent {page.bits} /Filter {page.filter} "
                f"/Length {len(page.stream)} >>\nstream\n"
            ).encode("ascii")
            + page.stream
            + b"\nendstream"
        )
        drawing = f"q {width_pt:.3f} 0 0 {height_pt:.3f} 0 0 cm /Im{number} Do Q".encode("ascii")
        content = add(
            f"<< /Length {len(drawing)} >>\nstream\n".encode("ascii") + drawing + b"\nendstream"
        )
        kids.append(
            add(
                (
                    f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {width_pt:.3f} {height_pt:.3f}] "
                    f"/Resources << /XObject << /Im{number} {image} 0 R >> >> "
                    f"/Contents {content} 0 R >>"
                ).encode("ascii")
            )
        )
    objects[1] = (
        f"<< /Type /Pages /Kids [{' '.join(f'{k} 0 R' for k in kids)}] /Count {len(kids)} >>"
    ).encode("ascii")
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(f"{number} 0 obj\n".encode("ascii") + body + b"\nendobj\n")
    xref = out.tell()
    out.write(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode("ascii"))
    for offset in offsets:
        out.write(f"{offset:010d} 00000 n \n".encode("ascii"))
    out.write(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode(
            "ascii"
        )
    )
    return out.getvalue()


# Deciding


def _choose(image: Image.Image, kind: str, profile: Profile) -> tuple[_Page, str, Image.Image]:
    """The page's treatment, its label, and how it will look once decoded."""
    if kind in ("blank", "line_art"):
        two = _bilevel(image)
        threshold = profile.text_ssim
        if kind == "blank" or ssim(image, two.convert("L")) >= threshold:
            return _page_from_bilevel(two), "bilevel", two.convert("L")
    threshold = profile.photo_ssim if kind == "photographic" else profile.text_ssim
    best: tuple[_Page, str, Image.Image] | None = None
    for quality in profile.jpeg_ladder:
        page, data = _page_from_jpeg(image, quality)
        decoded = Image.open(io.BytesIO(data))
        decoded.load()
        best = (page, f"jpeg-{quality}", decoded)
        if (
            ssim(image, decoded.convert(image.mode if image.mode in ("L", "RGB") else "RGB"))
            >= threshold
        ):
            break
    assert best is not None
    return best


def _threshold(kind: str, profile: Profile) -> float:
    return profile.photo_ssim if kind == "photographic" else profile.text_ssim


def _render_page(page: Any, dpi: int) -> Image.Image:
    """A page as pixels, cut to exactly the area its content covers.

    pdfium rounds the bitmap up, so it can be a pixel wider than the page really is at this scale.
    Judging or rebuilding from that spare pixel would squeeze the page and blur it, so it is cut.
    Image smoothing is off, so both sides of a comparison are drawn the same way.
    """
    bitmap = page.render(scale=dpi / 72, no_smoothimage=True).to_pil().convert("RGB")
    width_pt, height_pt = page.get_size()
    width = max(1, min(bitmap.width, int(width_pt * dpi / 72 + 1e-3)))
    height = max(1, min(bitmap.height, int(height_pt * dpi / 72 + 1e-3)))
    cropped: Image.Image = bitmap.crop((0, 0, width, height))
    return cropped


def _render_pdf(
    path: Any, dpi: int
) -> tuple[list[Image.Image], list[tuple[float, float]], str | None]:
    """The pages as images and their sizes in points, or a reason why the file cannot be treated."""
    try:
        pdf = pdfium.PdfDocument(str(path))
    except pdfium.PdfiumError:
        return [], [], "The PDF is damaged or protected with a password."
    images: list[Image.Image] = []
    sizes: list[tuple[float, float]] = []
    try:
        if len(pdf) > MAX_PAGES:
            return [], [], f"More than {MAX_PAGES} pages."
        for index in range(len(pdf)):
            page = pdf[index]
            try:
                text = page.get_textpage()
                try:
                    native = text.get_text_range().strip()
                finally:
                    text.close()
                if len(native) >= MIN_NATIVE_CHARACTERS:
                    return (
                        [],
                        [],
                        (
                            "This file has real text on its pages. Pictures of it would lose "
                            "the text, and no engine is installed that can shrink it safely."
                        ),
                    )
                images.append(_render_page(page, dpi))
                sizes.append((images[-1].width * 72 / dpi, images[-1].height * 72 / dpi))
            finally:
                page.close()
    finally:
        pdf.close()
    return images, sizes, None


def compress(path: Any, mime: str, source_size: int, profile: Profile | None = None) -> Result:
    """Makes a smaller copy and checks it. The file at ``path`` is only read."""
    profile = profile or Profile()
    started = time.monotonic()
    result = Result("unsupported", source_size=source_size)
    if mime in PDF_TYPES:
        pages_in, sizes, why = _render_pdf(path, profile.dpi)
        if why is not None:
            result.reason = why
            result.status = "no_benefit"
            result.seconds = time.monotonic() - started
            return result
        return _finish_pdf(result, pages_in, sizes, profile, started)
    if mime in IMAGE_TYPES:
        return _finish_image(result, path, profile, started)
    result.reason = "This kind of file cannot be compressed."
    result.seconds = time.monotonic() - started
    return result


def _finish_pdf(
    result: Result,
    images: list[Image.Image],
    sizes: list[tuple[float, float]],
    profile: Profile,
    started: float,
) -> Result:
    chosen: list[_Page] = []
    decoded: list[Image.Image] = []
    for number, image in enumerate(images, start=1):
        kind = classify(image)
        page, label, seen = _choose(image, kind, profile)
        chosen.append(page)
        decoded.append(seen)
        measured = PageResult(number, kind, label, threshold=_threshold(kind, profile))
        # How good the copy is: what a reader decodes, against the page at the working resolution.
        measured.ssim, measured.psnr = ssim(image, seen), psnr(image, seen)
        measured.passed = measured.ssim >= measured.threshold
        result.pages.append(measured)
    data = write_pdf(chosen, sizes)
    result.data, result.mime = data, "application/pdf"
    # Whether it displays like the original: draw the finished file again and compare it, at a
    # reduced size so that a pixel of rounding in a reader's drawing cannot count against it.
    pages_out, why = _render_pdf_any(data, profile.dpi)
    if why is not None or len(pages_out) != len(images):
        result.status, result.reason = "rejected", why or "The copy has a different page count."
        result.seconds = time.monotonic() - started
        return result
    for measured, original, copy in zip(result.pages, images, pages_out, strict=True):
        slack = DISPLAY_SIZE_SLACK
        if abs(copy.width - original.width) > slack or abs(copy.height - original.height) > slack:
            measured.display_ssim, measured.passed = 0.0, False
            continue
        measured.display_ssim = _display_ssim(original, copy)
        if measured.display_ssim < DISPLAY_MIN_SSIM:
            measured.passed = False
    return _decide(result, started, profile)


def _display_ssim(original: Image.Image, drawn: Image.Image) -> float:
    """Similarity of the original and the finished page as drawn, at a reduced size."""
    fitted = drawn.resize(original.size) if drawn.size != original.size else drawn
    return ssim(original.reduce(DISPLAY_REDUCE), fitted.reduce(DISPLAY_REDUCE))


def _render_pdf_any(data: bytes, dpi: int) -> tuple[list[Image.Image], str | None]:
    try:
        pdf = pdfium.PdfDocument(data)
    except pdfium.PdfiumError:
        return [], "The copy could not be opened again."
    images = []
    try:
        for index in range(len(pdf)):
            page = pdf[index]
            try:
                images.append(_render_page(page, dpi))
            finally:
                page.close()
    finally:
        pdf.close()
    return images, None


def _finish_image(result: Result, path: Any, profile: Profile, started: float) -> Result:
    try:
        with Image.open(path) as source:
            if getattr(source, "n_frames", 1) > 1:
                result.status = "no_benefit"
                result.reason = "Images with several frames are not handled yet."
                result.seconds = time.monotonic() - started
                return result
            if source.width * source.height > MAX_PIXELS:
                result.reason = "The image is too large to compress here."
                result.status = "no_benefit"
                result.seconds = time.monotonic() - started
                return result
            image = ImageOps.exif_transpose(source).convert("RGB")
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        result.reason = "The image is damaged and cannot be read."
        result.status = "no_benefit"
        result.seconds = time.monotonic() - started
        return result
    kind = classify(image)
    page, label, _decoded = _choose(image, kind, profile)
    page_result = PageResult(1, kind, label, threshold=_threshold(kind, profile))
    out = io.BytesIO()
    if page.filter == "/DCTDecode":
        out.write(page.stream)
        result.mime = "image/jpeg"
        copy = Image.open(io.BytesIO(page.stream)).convert("RGB")
    else:
        bilevel = _bilevel(image)
        bilevel.save(out, "PNG", optimize=True)
        result.mime = "image/png"
        copy = Image.open(io.BytesIO(out.getvalue())).convert("RGB")
    page_result.ssim, page_result.psnr = ssim(image, copy), psnr(image, copy)
    page_result.passed = page_result.ssim >= page_result.threshold
    result.pages.append(page_result)
    result.data = out.getvalue()
    return _decide(result, started, profile)


def _decide(result: Result, started: float, profile: Profile) -> Result:
    result.seconds = time.monotonic() - started
    failed = [p for p in result.pages if not p.passed]
    if failed:
        worst = min(failed, key=lambda p: p.ssim)
        result.status = "rejected"
        if worst.ssim >= worst.threshold:
            result.reason = (
                f"Page {worst.number} does not display like the original "
                f"(display similarity {worst.display_ssim or 0:.3f}, needs {DISPLAY_MIN_SSIM})."
            )
        else:
            result.reason = (
                f"Page {worst.number} looks too different from the original "
                f"(similarity {worst.ssim:.3f}, needs {worst.threshold})."
            )
        return result
    if result.source_size and result.derivative_size > result.source_size * (
        1 - profile.min_saving
    ):
        result.status = "no_benefit"
        saved = 1 - result.derivative_size / result.source_size
        result.reason = (
            f"The copy is {saved:.0%} smaller, and at least {profile.min_saving:.0%} is needed."
            if saved > 0
            else "The copy is not smaller than the original."
        )
        return result
    result.status = "accepted"
    return result
