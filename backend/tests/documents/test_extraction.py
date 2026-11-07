"""Reading text out of files: what is used as is, what is recognised, and in which languages."""

from __future__ import annotations

from pathlib import Path

import pypdfium2 as pdfium
import pytest

from docuflow.modules.documents.extraction import (
    ExtractionSettings,
    Extractor,
    UnreadableFile,
    is_blank,
    is_reliable,
)
from docuflow.modules.documents.ocr import (
    OcrFailed,
    TesseractEngine,
    parse_languages,
    parse_tsv,
)
from docuflow.platform.errors import DomainError
from tests.documents.files import (
    FakeEngine,
    drawn_page,
    mark_page,
    pdf_of_images,
    png_of,
    text_pdf,
    tiff_of_images,
)

SETTINGS = ExtractionSettings(default_languages=("eng", "fra"), max_languages=5, dpi=100)
LONG = "Invoice 1042 from Supplier ABC for consulting services, due on 30 November 2026."


def extract(
    tmp_path: Path,
    data: bytes,
    mime: str,
    engine: FakeEngine,
    languages: tuple[str, ...] = ("eng",),
):
    path = tmp_path / "source"
    path.write_bytes(data)
    return list(Extractor(engine, languages, SETTINGS).pages(path, mime))


# Parsing


def test_tesseract_tsv_becomes_text_in_reading_order_with_boxes() -> None:
    header = "\t".join(
        "level page_num block_num par_num line_num word_num left top width height conf text".split()
    )
    rows = [
        header,
        "1\t1\t0\t0\t0\t0\t0\t0\t900\t400\t-1\t",
        "5\t1\t1\t1\t1\t1\t40\t40\t80\t30\t96.5\tInvoice",
        "5\t1\t1\t1\t1\t2\t130\t40\t50\t30\t91\t1042",
        "5\t1\t1\t1\t2\t1\t40\t90\t90\t30\t88\tSupplier",
        "5\t1\t2\t1\t1\t1\t40\t200\t60\t30\t70\tTotal",
        "5\t1\t2\t1\t1\t2\t110\t200\t40\t30\t-1\t",
    ]
    page = parse_tsv("\n".join(rows))
    assert page.text == "Invoice 1042\nSupplier\n\nTotal"
    assert [word.text for word in page.words] == ["Invoice", "1042", "Supplier", "Total"]
    assert page.words[0].confidence == pytest.approx(0.965)
    assert page.confidence == pytest.approx((0.965 + 0.91 + 0.88 + 0.70) / 4, abs=0.001)


def test_tsv_with_nothing_readable_gives_empty_text_and_no_confidence() -> None:
    page = parse_tsv("level\tpage_num\n1\t1")
    assert (page.text, page.words, page.confidence) == ("", [], None)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("eng", ["eng"]),
        ("eng, fra+deu", ["eng", "fra", "deu"]),
        (["eng", "eng", "chi_sim"], ["eng", "chi_sim"]),
        ("srp_latn,ara", ["srp_latn", "ara"]),
    ],
)
def test_language_codes_are_parsed(raw: str | list[str], expected: list[str]) -> None:
    assert parse_languages(raw) == expected


@pytest.mark.parametrize("raw", ["english", "eng;rm -rf", "../../etc", "e"])
def test_things_that_are_not_language_codes_are_refused(raw: str) -> None:
    with pytest.raises(DomainError):
        parse_languages(raw)


def test_real_text_is_told_from_an_empty_or_broken_layer() -> None:
    assert is_reliable(LONG)
    assert not is_reliable("")
    assert not is_reliable("   \n  ")
    assert not is_reliable("ab")
    assert not is_reliable("����������" * 4)


def test_a_page_with_no_ink_is_blank() -> None:
    assert is_blank(drawn_page())
    assert not is_blank(mark_page())


# Reading files with a stand-in engine


def test_a_pdf_with_text_is_read_without_recognition(tmp_path: Path) -> None:
    engine = FakeEngine()
    pages = extract(tmp_path, text_pdf([LONG, "Second page " + LONG]), "application/pdf", engine)
    assert [page.number for page in pages] == [1, 2]
    assert all(page.source == "native" and page.has_native_text for page in pages)
    assert "Supplier ABC" in pages[0].text
    assert pages[1].text.startswith("Second page")
    assert engine.calls == []


def test_a_scanned_pdf_is_recognised_in_the_languages_chosen(tmp_path: Path) -> None:
    engine = FakeEngine()
    scanned = pdf_of_images([mark_page(), mark_page()])
    pages = extract(tmp_path, scanned, "application/pdf", engine, ("eng", "deu"))
    assert [page.source for page in pages] == ["fake", "fake"]
    assert [page.language for page in pages] == ["eng+deu", "eng+deu"]
    assert [call[1] for call in engine.calls] == [["eng", "deu"], ["eng", "deu"]]
    assert pages[0].text == "scanned page 1 read as eng+deu"
    assert pages[0].words[0]["t"] == "scanned"
    assert pages[0].engine_version == "fake 1.0"


def test_a_mixed_pdf_is_recognised_only_on_its_scanned_page(tmp_path: Path) -> None:
    first, second = tmp_path / "text.pdf", tmp_path / "scan.pdf"
    first.write_bytes(text_pdf([LONG]))
    second.write_bytes(pdf_of_images([mark_page()]))
    mixed = pdfium.PdfDocument(str(first))
    mixed.import_pages(pdfium.PdfDocument(str(second)))
    mixed.save(str(tmp_path / "mixed.pdf"))
    mixed.close()

    engine = FakeEngine()
    pages = extract(tmp_path, (tmp_path / "mixed.pdf").read_bytes(), "application/pdf", engine)
    assert [page.source for page in pages] == ["native", "fake"]
    assert len(engine.calls) == 1


def test_blank_pages_are_noted_and_not_recognised(tmp_path: Path) -> None:
    engine = FakeEngine()
    pages = extract(tmp_path, pdf_of_images([drawn_page(), mark_page()]), "application/pdf", engine)
    assert [page.is_blank for page in pages] == [True, False]
    assert len(engine.calls) == 1


def test_every_frame_of_a_multi_page_tiff_is_read(tmp_path: Path) -> None:
    engine = FakeEngine()
    data = tiff_of_images([mark_page(), mark_page(), mark_page()])
    pages = extract(tmp_path, data, "image/tiff", engine, ("fra",))
    assert [page.number for page in pages] == [1, 2, 3]
    assert [call[1] for call in engine.calls] == [["fra"]] * 3


def test_a_photo_is_read_as_one_page(tmp_path: Path) -> None:
    engine = FakeEngine()
    pages = extract(tmp_path, png_of(mark_page()), "image/png", engine)
    assert len(pages) == 1
    assert (pages[0].width, pages[0].height) == (600, 300)


def test_a_text_file_is_its_own_text(tmp_path: Path) -> None:
    engine = FakeEngine()
    pages = extract(tmp_path, "Café — résumé\nsecond line\n".encode(), "text/plain", engine)
    assert pages[0].text == "Café — résumé\nsecond line"
    assert pages[0].source == "native"
    assert engine.calls == []


def test_a_damaged_pdf_or_image_cannot_be_read(tmp_path: Path) -> None:
    engine = FakeEngine()
    with pytest.raises(UnreadableFile):
        extract(tmp_path, b"%PDF-1.7 this is not a pdf", "application/pdf", engine)
    with pytest.raises(UnreadableFile):
        extract(tmp_path, b"\x89PNG\r\n\x1a\n broken", "image/png", engine)


def test_scanned_pages_need_at_least_one_installed_language(tmp_path: Path) -> None:
    with pytest.raises(OcrFailed, match="installed"):
        extract(tmp_path, png_of(mark_page()), "image/png", FakeEngine(), ())


def test_the_page_limit_is_respected(tmp_path: Path) -> None:
    path = tmp_path / "many"
    path.write_bytes(text_pdf([LONG] * 5))
    limited = ExtractionSettings(max_pages=2, dpi=100)
    pages = list(Extractor(FakeEngine(), ("eng",), limited).pages(path, "application/pdf"))
    assert len(pages) == 2


# The real engine, where it is installed (CI installs it)

tesseract = TesseractEngine()
needs_tesseract = pytest.mark.skipif(
    not tesseract.available(), reason="Tesseract is not installed here"
)


@needs_tesseract
def test_tesseract_lists_what_is_installed_and_hides_its_helper_models() -> None:
    languages = tesseract.languages()
    assert "eng" in languages
    assert "osd" not in languages
    assert tesseract.version().lower().startswith("tesseract")


@needs_tesseract
def test_tesseract_reads_english_text_with_boxes_and_confidence() -> None:
    page = tesseract.recognise(drawn_page("Invoice 1042\nSupplier ABC"), ["eng"])
    assert "Invoice" in page.text
    assert "1042" in page.text
    assert "Supplier" in page.text
    assert page.confidence is not None
    assert page.confidence > 0.6
    assert all(word.w > 0 and word.h > 0 for word in page.words)


@needs_tesseract
@pytest.mark.parametrize(
    ("language", "text", "expected"),
    [
        ("fra", "Facture numéro 2026\nsociété anonyme", "société"),
        ("deu", "Rechnung für Müller\nStraße zwölf", "Müller"),
        ("spa", "Factura número 2026\ncompañía", "compañía"),
    ],
)
def test_tesseract_reads_other_languages_when_their_packs_are_installed(
    language: str, text: str, expected: str
) -> None:
    if language not in tesseract.languages():
        pytest.skip(f"the {language} language pack is not installed here")
    page = tesseract.recognise(drawn_page(text, (1100, 400)), [language])
    assert expected.lower() in page.text.lower()


@needs_tesseract
def test_tesseract_reads_with_several_languages_at_once() -> None:
    wanted = {"eng", "fra"}
    if not wanted <= set(tesseract.languages()):
        pytest.skip("English and French packs are not both installed here")
    page = tesseract.recognise(drawn_page("Invoice 1042\nfacture société"), ["eng", "fra"])
    assert "Invoice" in page.text
    assert "facture" in page.text.lower()


@needs_tesseract
def test_tesseract_refuses_a_language_it_does_not_have() -> None:
    with pytest.raises(OcrFailed):
        tesseract.recognise(drawn_page("Invoice"), ["zzz"])
