import zipfile
from pathlib import Path

import pymupdf
from ebooklib import epub

from pdftoepub.cli import main
from pdftoepub.converter import convert_pdf_to_epub, preview_pdf
from pdftoepub.models import EncryptedPdfError


def _make_pdf(path: Path) -> Path:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 80), "River Stories", fontsize=28)
    page.insert_text((72, 130), "A travel journal", fontsize=14)
    page.insert_text(
        (72, 180),
        "The ferry left before sunrise. Mist hung over the water and the town bells were still.",
        fontsize=11,
    )
    page = doc.new_page()
    page.insert_text((72, 80), "Chapter Two", fontsize=22)
    page.insert_text(
        (72, 140),
        "By noon the current had widened. We ate bread and olives on the deck.",
        fontsize=11,
    )
    doc.set_metadata({"title": "River Stories", "author": "Ada Ferry"})
    doc.save(path)
    doc.close()
    return path


def test_convert_creates_epub(tmp_path: Path) -> None:
    pdf = _make_pdf(tmp_path / "river.pdf")
    out = tmp_path / "river.epub"
    written = convert_pdf_to_epub(pdf, output=out)
    assert written.exists()
    assert written.stat().st_size > 500

    book = epub.read_epub(str(written))
    assert book.title == "River Stories"
    authors = [value for value, _attrs in book.get_metadata("DC", "creator")]
    assert "Ada Ferry" in authors
    with zipfile.ZipFile(written) as archive:
        chapter_html = archive.read("EPUB/chap_001.xhtml").decode("utf-8")
    assert "style/book.css" in chapter_html
    assert "ferry left before sunrise" in chapter_html


def test_preview_reads_metadata(tmp_path: Path) -> None:
    pdf = _make_pdf(tmp_path / "river.pdf")
    info = preview_pdf(pdf)
    assert info["title"] == "River Stories"
    assert info["author"] == "Ada Ferry"
    assert info["page_count"] == 2
    assert info["has_text"] is True
    assert info["chapter_count"] >= 1
    assert info["image_count"] >= 0
    assert isinstance(info["chapters"], list)
    assert info["chapters"]
    assert isinstance(info["preview_html"], str)
    assert info["preview_html"]
    assert "<" in info["preview_html"]


def test_encrypted_pdf_is_rejected(tmp_path: Path) -> None:
    raw = tmp_path / "plain.pdf"
    _make_pdf(raw)
    locked = tmp_path / "locked.pdf"
    src = pymupdf.open(raw)
    src.save(locked, encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="secret", owner_pw="owner")
    src.close()
    try:
        preview_pdf(locked)
        raised = False
    except EncryptedPdfError:
        raised = True
    assert raised


def test_cli_preview_and_convert(tmp_path: Path, capsys) -> None:
    pdf = _make_pdf(tmp_path / "river.pdf")
    out = tmp_path / "out.epub"
    assert main(["preview", str(pdf)]) == 0
    printed = capsys.readouterr().out
    assert "River Stories" in printed
    assert main(["convert", str(pdf), "-o", str(out)]) == 0
    assert out.exists()
