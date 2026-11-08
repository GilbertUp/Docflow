"""The compression engine on synthetic scans: what it chooses, and what it refuses to keep."""

from __future__ import annotations

from pathlib import Path

import pypdfium2 as pdfium
import pytest
from PIL import Image, ImageFilter

from docuflow.modules.compression import engine
from tests.compression import scans
from tests.documents.files import text_pdf

PDF = "application/pdf"


# A profile that no lossy copy of a photograph can meet, to exercise the quality gate.
STRICT = engine.Profile(photo_ssim=0.9999, jpeg_ladder=(30,))


def run(
    tmp_path: Path, data: bytes, mime: str = PDF, profile: engine.Profile | None = None
) -> engine.Result:
    return engine.compress(scans.write(tmp_path, data), mime, len(data), profile)


# Measuring


def test_identical_images_are_perfectly_similar_and_blur_lowers_it() -> None:
    page = scans.text_page()
    assert engine.ssim(page, page) == 1.0
    assert engine.psnr(page, page) == 99.0
    blurred = page.filter(ImageFilter.GaussianBlur(3))
    assert engine.ssim(page, blurred) < 0.9
    assert engine.psnr(page, blurred) < 40


def test_images_of_different_sizes_cannot_be_compared() -> None:
    with pytest.raises(ValueError, match="same size"):
        engine.ssim(Image.new("L", (10, 10)), Image.new("L", (12, 10)))


def test_a_page_is_blank_line_art_or_photographic() -> None:
    assert engine.classify(scans.blank_page()) == "blank"
    assert engine.classify(scans.text_page()) == "line_art"
    assert engine.classify(scans.photo_page()) == "photographic"


# Choosing and checking


def test_a_scanned_text_document_becomes_two_colour_pages_and_passes(tmp_path: Path) -> None:
    original = scans.scanned_pdf([scans.text_page(1), scans.text_page(2), scans.blank_page()])
    result = run(tmp_path, original)
    assert result.status == "accepted", result.reason
    assert [p.kind for p in result.pages] == ["line_art", "line_art", "blank"]
    assert {p.treatment for p in result.pages} == {"bilevel"}
    metrics = result.metrics()
    assert metrics["worst_ssim"] >= 0.97
    assert metrics["ratio"] < 0.5
    assert metrics["reduction"] > 0.5
    assert result.mime == PDF
    assert result.data is not None
    copy = pdfium.PdfDocument(result.data)
    assert len(copy) == 3
    copy.close()


def test_a_photographic_page_uses_the_gentlest_quality_that_passes(tmp_path: Path) -> None:
    original = scans.scanned_pdf([scans.photo_page()], quality=98)
    result = run(tmp_path, original)
    assert result.status == "accepted", result.reason
    page = result.pages[0]
    assert page.kind == "photographic"
    assert page.treatment.startswith("jpeg-")
    assert page.ssim >= 0.92
    assert result.derivative_size < len(original)


def test_a_page_that_cannot_meet_the_check_is_rejected_but_kept(tmp_path: Path) -> None:
    original = scans.scanned_pdf([scans.photo_page()], quality=98)
    result = run(tmp_path, original, profile=STRICT)
    assert result.status == "rejected"
    assert result.reason is not None
    assert "Page 1 looks too different" in result.reason
    assert result.data is not None  # kept, with its numbers, for study
    assert result.metrics()["worst_ssim"] < 0.9999
    assert not result.pages[0].passed


def test_the_worst_page_decides(tmp_path: Path) -> None:
    original = scans.grey_flate_pdf([scans.text_page(), scans.text_page(4), scans.photo_page()])
    result = run(tmp_path, original, profile=STRICT)
    assert result.status == "rejected"
    assert result.reason is not None
    assert "Page 3" in result.reason
    good = [p for p in result.pages if p.number != 3]
    assert all(p.passed for p in good)  # two good pages cannot excuse the third
    assert result.metrics()["worst_ssim"] == round(result.pages[2].ssim, 4)
    assert result.metrics()["mean_ssim"] > result.metrics()["worst_ssim"]


def test_a_copy_that_does_not_display_like_the_original_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = scans.grey_flate_pdf([scans.text_page()])
    # A reader that draws the copy as a blank page.
    monkeypatch.setattr(
        engine,
        "_render_pdf_any",
        lambda data, dpi: ([Image.new("RGB", scans.SIZE, "white")], None),
    )
    result = run(tmp_path, original)
    assert result.status == "rejected"
    assert result.reason is not None
    assert "does not display like the original" in result.reason
    assert result.pages[0].ssim >= 0.97  # the pixels were fine, the display was not


def test_a_copy_that_is_not_clearly_smaller_is_no_benefit(tmp_path: Path) -> None:
    page = scans.text_page()
    already_small = engine.write_pdf(
        [engine._page_from_bilevel(engine._bilevel(page))],
        [(page.width * 72 / scans.DPI, page.height * 72 / scans.DPI)],
    )
    result = run(tmp_path, already_small)
    assert result.status == "no_benefit"
    assert result.reason is not None
    assert "smaller" in result.reason
    assert result.metrics()["worst_ssim"] >= 0.97  # it was fine, just pointless


def test_a_file_with_real_text_is_left_alone(tmp_path: Path) -> None:
    result = run(tmp_path, text_pdf(["Invoice from Supplier ABC for consulting services."]))
    assert result.status == "no_benefit"
    assert result.data is None
    assert result.reason is not None
    assert "real text" in result.reason


def test_a_damaged_or_unsupported_file_is_reported_not_crashed(tmp_path: Path) -> None:
    broken = run(tmp_path, b"%PDF-1.4 this is not really a pdf")
    assert broken.status == "no_benefit"
    assert broken.reason is not None
    assert "damaged" in broken.reason
    other = run(tmp_path, b"hello", "text/plain")
    assert other.status == "unsupported"


# Images


def test_a_line_art_image_becomes_a_small_two_colour_png(tmp_path: Path) -> None:
    data = scans.png_bytes(scans.text_page())
    result = run(tmp_path, data, "image/png")
    assert result.status == "accepted", result.reason
    assert result.mime == "image/png"
    assert result.derivative_size < len(data)
    assert result.pages[0].ssim >= 0.97


def test_a_photograph_is_recompressed_as_jpeg(tmp_path: Path) -> None:
    data = scans.jpeg_bytes(scans.photo_page(), 98)
    result = run(tmp_path, data, "image/jpeg")
    assert result.status == "accepted", result.reason
    assert result.mime == "image/jpeg"
    assert result.derivative_size < len(data)


def test_an_image_with_several_frames_is_not_handled(tmp_path: Path) -> None:
    out = tmp_path / "multi.tiff"
    first, second = scans.text_page(1), scans.text_page(2)
    first.save(out, "TIFF", save_all=True, append_images=[second])
    result = engine.compress(out, "image/tiff", out.stat().st_size)
    assert result.status == "no_benefit"
    assert result.reason is not None
    assert "several frames" in result.reason


# Repeating a run


def test_the_profile_and_measurements_are_recorded_so_a_run_can_be_repeated(
    tmp_path: Path,
) -> None:
    result = run(tmp_path, scans.scanned_pdf([scans.text_page()]))
    params = engine.Profile().as_params()
    assert params["profile"] == "standard"
    assert params["dpi"] == scans.DPI
    assert params["jpeg_ladder"] == [80, 65, 50, 40]
    metrics = result.metrics()
    assert metrics["per_page"][0]["treatment"] == "bilevel"
    assert metrics["per_page"][0]["threshold"] == 0.97
    again = run(tmp_path, scans.scanned_pdf([scans.text_page()]))
    assert again.data == result.data  # same input and profile, same copy
