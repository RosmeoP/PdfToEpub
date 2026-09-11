from pathlib import Path

from pdftoepub.cli import main
from pdftoepub.jobs import collect_pdfs, convert_many, destination_for, should_skip
from tests.test_converter import _make_pdf


def test_collect_pdfs_from_folder(tmp_path: Path) -> None:
    _make_pdf(tmp_path / "a.pdf")
    _make_pdf(tmp_path / "b.pdf")
    (tmp_path / "notes.txt").write_text("nope")
    pdfs, errors = collect_pdfs([tmp_path])
    assert [p.name for p in pdfs] == ["a.pdf", "b.pdf"]
    assert errors == []


def test_collect_missing_file(tmp_path: Path) -> None:
    pdfs, errors = collect_pdfs([tmp_path / "missing.pdf"])
    assert pdfs == []
    assert errors


def test_batch_convert_folder(tmp_path: Path) -> None:
    folder = tmp_path / "books"
    folder.mkdir()
    _make_pdf(folder / "one.pdf")
    _make_pdf(folder / "two.pdf")
    out_dir = tmp_path / "epubs"
    assert main(["convert", str(folder), "--out-dir", str(out_dir)]) == 0
    assert (out_dir / "one.epub").exists()
    assert (out_dir / "two.epub").exists()


def test_skip_existing(tmp_path: Path) -> None:
    pdf = _make_pdf(tmp_path / "river.pdf")
    out = tmp_path / "river.epub"
    first = convert_many([pdf], output=out)
    assert first[0].output == out
    assert first[0].skipped is False
    second = convert_many([pdf], output=out, skip_existing=True)
    assert second[0].skipped is True


def test_destination_next_to_source(tmp_path: Path) -> None:
    pdf = tmp_path / "nested" / "book.pdf"
    assert destination_for(pdf, None, None) == tmp_path / "nested" / "book.epub"


def test_convert_many_accepts_quality_kwargs(tmp_path: Path) -> None:
    pdf = _make_pdf(tmp_path / "river.pdf")
    results = convert_many([pdf], output=tmp_path / "river.epub", ocr=False, use_cache=False)
    assert results[0].output == tmp_path / "river.epub"
    assert results[0].error is None


def test_should_skip_requires_newer_epub(tmp_path: Path) -> None:
    pdf = tmp_path / "book.pdf"
    epub_path = tmp_path / "book.epub"
    pdf.write_bytes(b"%PDF")
    epub_path.write_bytes(b"PK")
    assert should_skip(pdf, epub_path, True) is True
    assert should_skip(pdf, epub_path, False) is False
