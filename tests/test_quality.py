import zipfile
from pathlib import Path

import pymupdf

from pdftoepub.cache import extract_cache_stats
from pdftoepub.converter import (
    convert_document_to_epub,
    convert_pdf_to_epub,
    extract_pdf,
    ocr_is_available,
    preview_pdf,
)
from pdftoepub.extract import extract_document
from pdftoepub.layout import PageUnit, detect_column_gutter, reorder_two_column, try_parse_table
from tests.test_converter import _make_pdf


def _make_two_column_pdf(path: Path) -> Path:
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    left = (
        "The left bank was rocky and steep along the water.\n\n"
        "Willows leaned over the path and hid the ferry landing from view.\n\n"
        "More left text fills this column with several wrapped lines of words about stones.\n\n"
        "Left column continues with another sentence about lanterns and rope.\n\n"
        "LEFT-END-UNIQUE marks the bottom of the left column only."
    )
    right = (
        "RIGHT-START-UNIQUE marks the top of the right column only.\n\n"
        "The right bank was muddy and low beside the reeds.\n\n"
        "Herons stood in the shallows and ignored the passing boats.\n\n"
        "More right text fills this column with several wrapped lines of words about mist.\n\n"
        "Right column continues with another sentence about bells and fog."
    )
    page.insert_textbox(pymupdf.Rect(48, 72, 280, 740), left, fontsize=11)
    page.insert_textbox(pymupdf.Rect(330, 72, 564, 740), right, fontsize=11)
    doc.set_metadata({"title": "Two Columns", "author": "Test Author"})
    doc.save(path)
    doc.close()
    return path


def _make_table_pdf(path: Path) -> Path:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 80), "Cargo List", fontsize=22)
    rows = [
        ("Name", "Qty", "Price"),
        ("Apples", "3", "1.20"),
        ("Pears", "12", "0.80"),
        ("Plums", "7", "2.40"),
    ]
    y = 130
    for name, qty, price in rows:
        page.insert_text((72, y), name, fontsize=11)
        page.insert_text((260, y), qty, fontsize=11)
        page.insert_text((400, y), price, fontsize=11)
        y += 24
    doc.set_metadata({"title": "Cargo List", "author": "Test Author"})
    doc.save(path)
    doc.close()
    return path


def _solid_pixmap(width: int, height: int, color: tuple[int, int, int]) -> pymupdf.Pixmap:
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, width, height), False)
    pix.set_rect(pix.irect, color)
    return pix


def _make_cover_image_pdf(path: Path) -> Path:
    doc = pymupdf.open()
    page = doc.new_page()
    pix = _solid_pixmap(360, 480, (36, 92, 168))
    page.insert_image(pymupdf.Rect(80, 60, 440, 540), pixmap=pix)
    doc.set_metadata({"title": "Cover Image", "author": "Test Author"})
    doc.save(path)
    doc.close()
    return path


def _make_empty_text_pdf(path: Path) -> Path:
    doc = pymupdf.open()
    page = doc.new_page()
    pix = _solid_pixmap(400, 520, (20, 20, 20))
    page.insert_image(page.rect, pixmap=pix)
    doc.set_metadata({"title": "Scan Stand-in", "author": "Test Author"})
    doc.save(path)
    doc.close()
    return path


def test_two_column_reading_order_unit() -> None:
    left = [
        PageUnit(
            y=100 + i * 20,
            x=50,
            kind="text",
            text=f"Left column sentence number {i} about the river bank.",
            bbox=(50, 100 + i * 20, 250, 118 + i * 20),
        )
        for i in range(5)
    ]
    right = [
        PageUnit(
            y=100 + i * 20,
            x=330,
            kind="text",
            text=f"Right column sentence number {i} about the muddy shore.",
            bbox=(330, 100 + i * 20, 530, 118 + i * 20),
        )
        for i in range(5)
    ]
    interleaved: list[PageUnit] = []
    for left_unit, right_unit in zip(left, right):
        interleaved.extend([left_unit, right_unit])
    gutter = detect_column_gutter(interleaved, 612)
    assert gutter is not None
    ordered = reorder_two_column(interleaved, gutter, 612)
    texts = [unit.text for unit in ordered]
    assert texts[:5] == [unit.text for unit in left]
    assert texts[5:] == [unit.text for unit in right]
    assert texts.index(left[-1].text) < texts.index(right[0].text)


def test_two_column_pdf_keeps_left_then_right(tmp_path: Path) -> None:
    pdf = _make_two_column_pdf(tmp_path / "columns.pdf")
    document = extract_pdf(pdf, ocr=False)
    text = " ".join(
        block.text
        for chapter in document.chapters
        for block in chapter.blocks
        if block.kind in {"heading", "paragraph"}
    )
    assert "LEFT-END-UNIQUE" in text
    assert "RIGHT-START-UNIQUE" in text
    assert text.index("LEFT-END-UNIQUE") < text.index("RIGHT-START-UNIQUE")


def test_simple_table_emits_html_table(tmp_path: Path) -> None:
    header = [
        PageUnit(y=100, x=72, kind="text", text="Name", bbox=(72, 92, 120, 108), cells=[(72, "Name")]),
        PageUnit(y=100, x=260, kind="text", text="Qty", bbox=(260, 92, 290, 108), cells=[(260, "Qty")]),
        PageUnit(y=100, x=400, kind="text", text="Price", bbox=(400, 92, 450, 108), cells=[(400, "Price")]),
    ]
    apples = [
        PageUnit(y=124, x=72, kind="text", text="Apples", bbox=(72, 116, 130, 132), cells=[(72, "Apples")]),
        PageUnit(y=124, x=260, kind="text", text="3", bbox=(260, 116, 280, 132), cells=[(260, "3")]),
        PageUnit(y=124, x=400, kind="text", text="1.20", bbox=(400, 116, 440, 132), cells=[(400, "1.20")]),
    ]
    block, consumed = try_parse_table(header + apples)
    assert block is not None
    assert consumed == 6
    assert block.kind == "table"
    assert block.rows[0] == ["Name", "Qty", "Price"]

    pdf = _make_table_pdf(tmp_path / "table.pdf")
    out = tmp_path / "table.epub"
    convert_pdf_to_epub(pdf, output=out, ocr=False)
    with zipfile.ZipFile(out) as archive:
        html = b"".join(
            archive.read(name) for name in archive.namelist() if name.endswith(".xhtml")
        ).decode("utf-8")
    assert "<table>" in html
    assert "Apples" in html
    assert "1.20" in html


def test_cover_from_image_and_page_render(tmp_path: Path) -> None:
    image_pdf = _make_cover_image_pdf(tmp_path / "cover.pdf")
    image_doc = extract_pdf(image_pdf, ocr=False)
    assert image_doc.cover is not None
    assert image_doc.cover.width >= 160

    text_pdf = _make_pdf(tmp_path / "river.pdf")
    text_doc = extract_pdf(text_pdf)
    assert text_doc.cover is not None
    written = convert_document_to_epub(text_doc, output=tmp_path / "river.epub")
    with zipfile.ZipFile(written) as archive:
        names = [name.lower() for name in archive.namelist()]
    assert any("cover" in name for name in names)


def test_empty_text_page_keeps_image_fallback(tmp_path: Path) -> None:
    pdf = _make_empty_text_pdf(tmp_path / "scan.pdf")
    document = extract_pdf(pdf, ocr=False)
    assert document.has_text is False
    assert len(document.images) >= 1
    assert document.cover is not None
    written = convert_pdf_to_epub(pdf, output=tmp_path / "scan.epub", ocr=False)
    assert written.exists()


def test_preview_is_json_serializable_and_safe(tmp_path: Path) -> None:
    import json

    pdf = _make_pdf(tmp_path / "river.pdf")
    info = preview_pdf(pdf)
    dumped = json.dumps(info)
    assert "River Stories" in dumped
    assert "preview_html" in info
    assert "ferry left before sunrise" in info["preview_html"].lower() or "River" in info["preview_html"]


def test_extract_cache_skips_reparse(tmp_path: Path, monkeypatch) -> None:
    from pdftoepub import extract as extract_mod

    pdf = _make_pdf(tmp_path / "river.pdf")
    calls = {"n": 0}
    real = extract_mod._extract_page

    def wrapped(*args, **kwargs):
        calls["n"] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(extract_mod, "_extract_page", wrapped)
    extract_document(pdf)
    first = calls["n"]
    assert first > 0
    stats = extract_cache_stats()
    extract_document(pdf)
    assert calls["n"] == first
    assert extract_cache_stats()["hits"] == stats["hits"] + 1


def test_cache_does_not_keep_title_override(tmp_path: Path) -> None:
    pdf = _make_pdf(tmp_path / "river.pdf")
    first = extract_pdf(pdf, title="Override Title")
    assert first.title == "Override Title"
    second = extract_pdf(pdf)
    assert second.title == "River Stories"


def test_preview_then_convert_reuses_cache(tmp_path: Path, monkeypatch) -> None:
    from pdftoepub import extract as extract_mod

    pdf = _make_pdf(tmp_path / "river.pdf")
    calls = {"n": 0}
    real = extract_mod._extract_page

    def wrapped(*args, **kwargs):
        calls["n"] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(extract_mod, "_extract_page", wrapped)
    preview_pdf(pdf)
    first = calls["n"]
    convert_pdf_to_epub(pdf, output=tmp_path / "river.epub")
    assert calls["n"] == first


def test_ocr_flag_does_not_require_tesseract(tmp_path: Path) -> None:
    pdf = _make_empty_text_pdf(tmp_path / "scan.pdf")
    document = extract_pdf(pdf, ocr=True)
    assert document.images
    assert ocr_is_available() in {True, False}


def test_convert_from_extracted_document(tmp_path: Path) -> None:
    pdf = _make_pdf(tmp_path / "river.pdf")
    document = extract_pdf(pdf)
    written = convert_document_to_epub(document, output=tmp_path / "from-doc.epub")
    assert written.exists()
    with zipfile.ZipFile(written) as archive:
        html = archive.read("EPUB/chap_001.xhtml").decode("utf-8")
    assert "ferry left before sunrise" in html
