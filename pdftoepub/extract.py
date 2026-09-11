from __future__ import annotations

import html
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

from pdftoepub.cache import cache_get, cache_put, extract_cache_key
from pdftoepub.layout import PageUnit, cells_from_spans, layout_page
from pdftoepub.models import (
    Block,
    Chapter,
    ConversionError,
    Document,
    EmptyDocumentError,
    EncryptedPdfError,
    ImageAsset,
)
from pdftoepub.ocr import ocr_page, page_needs_ocr
from pdftoepub.progress import ProgressFn, report

_SENTENCE_END = tuple(".?!…\"”’")
_HEADER_BAND = 0.09
_FOOTER_BAND = 0.09
_MIN_REPEAT_RATIO = 0.45
_IMAGE_EXTS = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "tif": "image/tiff",
    "tiff": "image/tiff",
    "bmp": "image/bmp",
    "webp": "image/webp",
}
_PREVIEW_BLOCKS = 8
_PREVIEW_PARA_CHARS = 500
_COVER_MIN_EDGE = 160
_COVER_MIN_AREA = 160 * 200


@dataclass
class _Line:
    text: str
    size: float
    bbox: tuple[float, float, float, float]
    page_index: int
    page_height: float
    flags: int
    cells: list[tuple[float, str]] = field(default_factory=list)


@dataclass
class _PageItem:
    y: float
    x: float
    kind: str
    line: _Line | None = None
    image: ImageAsset | None = None
    page_width: float = 612.0


def extract_document(
    source: Path | str | bytes,
    *,
    title: str | None = None,
    author: str | None = None,
    include_images: bool = True,
    source_name: str = "",
    progress: ProgressFn | None = None,
    ocr: bool = True,
    use_cache: bool = True,
) -> Document:
    """Extract a PDF into a Document.

    ``ocr`` tries Tesseract/pytesseract on pages with little text when those
    extras are installed; otherwise the usual image-page fallback is used.
    """
    cache_key = ""
    if use_cache:
        try:
            cache_key = extract_cache_key(
                source,
                include_images=include_images,
                ocr=ocr,
                source_name=source_name,
            )
        except OSError:
            cache_key = ""
        if cache_key:
            cached = cache_get(cache_key)
            if cached is not None:
                report(progress, 84, "Using cached extraction…")
                return _apply_overrides(cached, title, author)

    report(progress, 4, "Opening PDF…")
    doc = _open_pdf(source)
    try:
        document = _extract_open_document(
            doc,
            title=None,
            author=None,
            include_images=include_images,
            source_name=source_name,
            progress=progress,
            ocr=ocr,
        )
    finally:
        doc.close()

    if cache_key:
        cache_put(cache_key, document)
    return _apply_overrides(document, title, author)


def _extract_open_document(
    doc: pymupdf.Document,
    *,
    title: str | None,
    author: str | None,
    include_images: bool,
    source_name: str,
    progress: ProgressFn | None,
    ocr: bool,
) -> Document:
    meta = doc.metadata or {}
    page_count = doc.page_count
    if page_count == 0:
        raise EmptyDocumentError("The PDF has no pages.")

    images: list[ImageAsset] = []
    laid_out: list[PageUnit | Block] = []
    image_index = 0
    report(progress, 8, f"Reading {page_count} page{'s' if page_count != 1 else ''}…")

    for page_index, page in enumerate(doc):
        page_units, image_index = _extract_page(
            doc, page, page_index, image_index, include_images, ocr=ocr
        )
        laid_out.extend(page_units)
        for item in page_units:
            if isinstance(item, PageUnit) and item.image is not None:
                images.append(item.image)
        percent = 8 + int((page_index + 1) / page_count * 67)
        report(progress, percent, f"Reading page {page_index + 1} of {page_count}…")

    report(progress, 78, "Cleaning headers and rebuilding paragraphs…")
    lines = [
        _line_from_unit(item)
        for item in laid_out
        if isinstance(item, PageUnit) and item.kind == "text"
    ]
    repeats = _repeated_running_text(lines, page_count)
    body_size = _body_font_size(lines)
    heading_sizes = _heading_sizes(lines, body_size)

    blocks = _items_to_blocks(laid_out, repeats, body_size, heading_sizes)
    if not blocks:
        raise EmptyDocumentError("No readable text or images were found in this PDF.")

    report(progress, 84, "Building chapters…")
    chapters = _split_chapters(blocks)
    has_text = any(
        (block.kind in {"heading", "paragraph"} and block.text.strip())
        or (block.kind == "table" and (block.rows or block.text.strip()))
        for block in blocks
    )
    cover = _choose_cover(doc, images)

    resolved_title = (title or "").strip() or _clean_meta(meta.get("title")) or _guess_title(
        chapters, source_name
    )
    resolved_author = (author or "").strip() or _clean_meta(meta.get("author")) or "Unknown"

    return Document(
        title=resolved_title,
        author=resolved_author,
        language=_language_from_meta(meta),
        page_count=page_count,
        chapters=chapters,
        images=images,
        has_text=has_text,
        source_name=source_name,
        cover=cover,
    )


def _apply_overrides(document: Document, title: str | None, author: str | None) -> Document:
    if title and title.strip():
        document.title = title.strip()
    if author and author.strip():
        document.author = author.strip()
    return document


def _open_pdf(source: Path | str | bytes) -> pymupdf.Document:
    try:
        if isinstance(source, bytes):
            doc = pymupdf.open(stream=source, filetype="pdf")
        else:
            doc = pymupdf.open(source)
    except Exception as exc:
        raise ConversionError(f"Could not open PDF: {exc}") from exc

    if doc.is_encrypted and not doc.authenticate(""):
        doc.close()
        raise EncryptedPdfError("This PDF is password-protected.")
    return doc


def _extract_page(
    doc: pymupdf.Document,
    page: pymupdf.Page,
    page_index: int,
    image_index: int,
    include_images: bool,
    ocr: bool = True,
) -> tuple[list[PageUnit | Block], int]:
    page_height = float(page.rect.height)
    page_width = float(page.rect.width)
    raw = page.get_text("dict", flags=pymupdf.TEXTFLAGS_DICT)
    items: list[_PageItem] = []
    used_xrefs: set[int] = set()

    for block in raw.get("blocks", []):
        if block.get("type") == 0:
            for line in block.get("lines", []):
                parsed = _line_from_spans(line, page_index, page_height)
                if parsed is None:
                    continue
                x0, y0, _, _ = parsed.bbox
                items.append(
                    _PageItem(
                        y=y0 + page_index * 10_000,
                        x=x0,
                        kind="text",
                        line=parsed,
                        page_width=page_width,
                    )
                )
        elif include_images and block.get("type") == 1:
            asset, image_index, xref = _image_from_block(doc, page, block, image_index)
            if asset is None:
                continue
            if xref is not None:
                used_xrefs.add(xref)
            x0, y0, _, _ = block.get("bbox", (0, 0, 0, 0))
            items.append(
                _PageItem(
                    y=y0 + page_index * 10_000,
                    x=x0,
                    kind="image",
                    image=asset,
                    page_width=page_width,
                )
            )

    if include_images:
        extras, image_index = _remaining_images(doc, page, page_index, image_index, used_xrefs)
        for extra in extras:
            extra.page_width = page_width
        items.extend(extras)

    page_text = " ".join(item.line.text for item in items if item.line is not None)
    if ocr and page_needs_ocr(page_text):
        ocr_items = _items_from_ocr(page, page_index, page_height, page_width)
        if ocr_items:
            images_only = [item for item in items if item.image is not None]
            items = images_only + ocr_items

    items.sort(key=lambda item: (round(item.y, 1), round(item.x, 1)))
    units = [_unit_from_item(item, page_width) for item in items]
    return layout_page(units, page_width), image_index


def _items_from_ocr(
    page: pymupdf.Page,
    page_index: int,
    page_height: float,
    page_width: float,
) -> list[_PageItem]:
    items: list[_PageItem] = []
    for ocr_line in ocr_page(page):
        text = _normalize_spaces(ocr_line.text).strip()
        if not text:
            continue
        x0, y0, x1, y1 = ocr_line.bbox
        line = _Line(
            text=text,
            size=ocr_line.size,
            bbox=(x0, y0, x1, y1),
            page_index=page_index,
            page_height=page_height,
            flags=0,
        )
        items.append(
            _PageItem(
                y=y0 + page_index * 10_000,
                x=x0,
                kind="text",
                line=line,
                page_width=page_width,
            )
        )
    return items


def _unit_from_item(item: _PageItem, page_width: float) -> PageUnit:
    line = item.line
    if line is not None:
        return PageUnit(
            y=item.y,
            x=item.x,
            kind="text",
            text=line.text,
            size=line.size,
            bbox=line.bbox,
            page_index=line.page_index,
            page_height=line.page_height,
            page_width=page_width,
            flags=line.flags,
            cells=list(line.cells),
        )
    image = item.image
    x0 = item.x
    y0 = item.y
    width = float(image.width) if image is not None else 1.0
    height = float(image.height) if image is not None else 1.0
    return PageUnit(
        y=item.y,
        x=item.x,
        kind="image",
        bbox=(x0, y0, x0 + width, y0 + height),
        page_width=page_width,
        image=image,
    )


def _line_from_unit(unit: PageUnit) -> _Line:
    return _Line(
        text=unit.text,
        size=unit.size,
        bbox=unit.bbox,
        page_index=unit.page_index,
        page_height=unit.page_height,
        flags=unit.flags,
        cells=list(unit.cells),
    )


def _line_from_spans(line: dict, page_index: int, page_height: float) -> _Line | None:
    spans = line.get("spans") or []
    parts: list[str] = []
    sizes: list[float] = []
    flags = 0
    for span in spans:
        text = (span.get("text") or "").replace("\u00ad", "")
        if not text.strip() and not parts:
            continue
        parts.append(text)
        if (span.get("text") or "").strip():
            sizes.append(float(span.get("size") or 0))
            flags |= int(span.get("flags") or 0)
    text = _normalize_spaces("".join(parts)).strip()
    if not text:
        return None
    bbox = tuple(line.get("bbox") or (0, 0, 0, 0))
    size = max(sizes) if sizes else 0.0
    return _Line(
        text=text,
        size=size,
        bbox=bbox,
        page_index=page_index,
        page_height=page_height,
        flags=flags,
        cells=cells_from_spans(spans, _normalize_spaces),
    )


def _image_from_block(
    doc: pymupdf.Document,
    page: pymupdf.Page,
    block: dict,
    image_index: int,
) -> tuple[ImageAsset | None, int, int | None]:
    xref = block.get("xref") or block.get("number")
    data = block.get("image")
    ext = (block.get("ext") or "png").lower()
    width = int(block.get("width") or 0)
    height = int(block.get("height") or 0)

    if data is None and xref:
        try:
            extracted = doc.extract_image(int(xref))
        except Exception:
            extracted = None
        if extracted:
            data = extracted.get("image")
            ext = (extracted.get("ext") or ext).lower()
            width = int(extracted.get("width") or width)
            height = int(extracted.get("height") or height)

    if not data or _tiny_or_decorative(width, height, page):
        return None, image_index, int(xref) if xref else None

    image_index += 1
    return _make_image(image_index, data, ext, width, height), image_index, int(xref) if xref else None


def _remaining_images(
    doc: pymupdf.Document,
    page: pymupdf.Page,
    page_index: int,
    image_index: int,
    used_xrefs: set[int],
) -> tuple[list[_PageItem], int]:
    items: list[_PageItem] = []
    try:
        infos = page.get_image_info(xrefs=True)
    except Exception:
        return items, image_index

    seen: set[int] = set()
    for info in infos:
        xref = int(info.get("xref") or 0)
        if not xref or xref in used_xrefs or xref in seen:
            continue
        seen.add(xref)
        try:
            extracted = doc.extract_image(xref)
        except Exception:
            continue
        data = extracted.get("image")
        if not data:
            continue
        width = int(extracted.get("width") or info.get("width") or 0)
        height = int(extracted.get("height") or info.get("height") or 0)
        if _tiny_or_decorative(width, height, page):
            continue
        bbox = info.get("bbox") or (0, 0, 0, 0)
        image_index += 1
        asset = _make_image(image_index, data, extracted.get("ext") or "png", width, height)
        items.append(
            _PageItem(
                y=float(bbox[1]) + page_index * 10_000,
                x=float(bbox[0]),
                kind="image",
                image=asset,
            )
        )
    return items, image_index


def _make_image(index: int, data: bytes, ext: str, width: int, height: int) -> ImageAsset:
    ext = (ext or "png").lower().lstrip(".")
    if ext == "jpeg":
        ext = "jpg"
    media = _IMAGE_EXTS.get(ext, "image/png")
    if media == "image/png" and ext not in _IMAGE_EXTS:
        ext = "png"
    return ImageAsset(
        uid=f"img_{index:04d}",
        file_name=f"images/img_{index:04d}.{ext}",
        media_type=media,
        data=data,
        width=width,
        height=height,
    )


def _tiny_or_decorative(width: int, height: int, page: pymupdf.Page) -> bool:
    if width < 24 or height < 24:
        return True
    if width * height < 80 * 80:
        return True
    page_area = max(page.rect.width * page.rect.height, 1)
    if width * height > page_area * 8:
        return True
    return False


def _is_substantial_cover(image: ImageAsset) -> bool:
    return (
        image.width >= _COVER_MIN_EDGE
        and image.height >= _COVER_MIN_EDGE
        and image.width * image.height >= _COVER_MIN_AREA
    )


def _choose_cover(doc: pymupdf.Document, images: list[ImageAsset]) -> ImageAsset | None:
    for image in images:
        if _is_substantial_cover(image):
            return image
    if doc.page_count < 1:
        return None
    return _render_page_cover(doc[0])


def _render_page_cover(page: pymupdf.Page) -> ImageAsset | None:
    try:
        zoom = min(2.0, 1200 / max(page.rect.width, 1))
        pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
        data = pix.tobytes("jpeg")
    except Exception:
        return None
    if not data:
        return None
    return ImageAsset(
        uid="cover",
        file_name="cover.jpg",
        media_type="image/jpeg",
        data=data,
        width=int(pix.width),
        height=int(pix.height),
    )


def _repeated_running_text(lines: list[_Line], page_count: int) -> set[str]:
    if page_count < 3 or not lines:
        return set()

    by_page: dict[int, list[_Line]] = {}
    for line in lines:
        by_page.setdefault(line.page_index, []).append(line)

    candidates: list[str] = []
    for page_lines in by_page.values():
        height = page_lines[0].page_height or 1
        for line in page_lines:
            y0 = line.bbox[1]
            y1 = line.bbox[3]
            near_edge = y0 <= height * _HEADER_BAND or y1 >= height * (1 - _FOOTER_BAND)
            if near_edge:
                candidates.append(_running_key(line.text))

    needed = max(2, int(page_count * _MIN_REPEAT_RATIO))
    counts = Counter(key for key in candidates if key)
    return {key for key, count in counts.items() if count >= needed}


def _running_key(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", text).strip().lower()
    cleaned = re.sub(r"\b\d{1,4}\b", "", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip(" -–—·•|")


def _is_page_number(text: str) -> bool:
    return bool(re.fullmatch(r"(?:page\s+)?\d{1,4}(?:\s*/\s*\d{1,4})?", text.strip(), re.I))


def _body_font_size(lines: list[_Line]) -> float:
    sizes = [round(line.size * 2) / 2 for line in lines if line.size > 0]
    if not sizes:
        return 11.0
    return Counter(sizes).most_common(1)[0][0]


def _heading_sizes(lines: list[_Line], body_size: float) -> list[float]:
    sizes = sorted(
        {
            round(line.size * 2) / 2
            for line in lines
            if line.size >= body_size + 1.2
        },
        reverse=True,
    )
    return sizes[:3]


def _heading_level(size: float, heading_sizes: list[float], body_size: float, flags: int) -> int:
    if heading_sizes:
        for index, heading_size in enumerate(heading_sizes):
            if abs(size - heading_size) <= 0.6:
                return index + 1
    if size >= body_size + 1.2:
        return min(3, 1 + int((size - body_size) // 3))
    # Bold, short, larger-than-body lines still count as subheads
    if (flags & 16) and size >= body_size + 0.4:
        return 3
    return 0


def _items_to_blocks(
    items: list[PageUnit | Block],
    repeats: set[str],
    body_size: float,
    heading_sizes: list[float],
) -> list[Block]:
    blocks: list[Block] = []
    pending: list[_Line] = []

    def flush() -> None:
        nonlocal pending
        if not pending:
            return
        text = _join_lines(pending)
        if text:
            size = max(line.size for line in pending)
            flags = 0
            for line in pending:
                flags |= line.flags
            level = _heading_level(size, heading_sizes, body_size, flags)
            if level and _looks_like_heading(text, pending):
                blocks.append(Block(kind="heading", text=text, level=level))
            else:
                blocks.append(Block(kind="paragraph", text=text))
        pending = []

    for item in items:
        if isinstance(item, Block):
            flush()
            blocks.append(item)
            continue
        if item.kind == "image" and item.image is not None:
            flush()
            blocks.append(Block(kind="image", image=item.image))
            continue
        if item.kind != "text" or not item.text:
            continue
        line = _line_from_unit(item)
        if _is_page_number(line.text) or _running_key(line.text) in repeats:
            continue
        if not pending:
            pending = [line]
            continue
        if _should_break_paragraph(pending[-1], line):
            flush()
            pending = [line]
        else:
            pending.append(line)
    flush()
    return _merge_adjacent_headings(blocks)


def _should_break_paragraph(prev: _Line, current: _Line) -> bool:
    if current.page_index != prev.page_index:
        return True
    prev_height = max(prev.bbox[3] - prev.bbox[1], 8)
    gap = current.bbox[1] - prev.bbox[3]
    if gap > prev_height * 1.35:
        return True
    if abs(current.size - prev.size) >= 1.4:
        return True
    return False


def _looks_like_heading(text: str, lines: list[_Line]) -> bool:
    if len(text) > 120:
        return False
    if text.count(".") > 2:
        return False
    if len(lines) > 3:
        return False
    return True


def _join_lines(lines: list[_Line]) -> str:
    parts: list[str] = []
    for line in lines:
        text = line.text.strip()
        if not parts:
            parts.append(text)
            continue
        prev = parts[-1]
        if prev.endswith("-") and text and text[0].islower():
            parts[-1] = prev[:-1] + text
        elif prev.endswith("-") and text[:1].isalpha():
            parts[-1] = prev[:-1] + text
        else:
            space = "" if prev.endswith(_SENTENCE_END) and text[:1] in "\"“‘'(" else " "
            parts[-1] = prev + space + text
    return _normalize_spaces(parts[0] if parts else "").strip()


def _split_chapters(blocks: list[Block]) -> list[Chapter]:
    chapters: list[Chapter] = []
    current = Chapter(title="Contents", blocks=[])

    def start_chapter(title: str, heading: Block | None = None) -> None:
        nonlocal current
        if current.blocks:
            chapters.append(current)
        current = Chapter(title=title or "Chapter", blocks=[])
        if heading is not None:
            current.blocks.append(heading)

    h1_count = sum(1 for block in blocks if block.kind == "heading" and block.level == 1)
    use_h1 = h1_count >= 2

    for block in blocks:
        if block.kind == "heading" and block.level == 1 and use_h1:
            start_chapter(block.text, block)
            continue
        current.blocks.append(block)

    if current.blocks:
        chapters.append(current)
    if not chapters:
        chapters.append(Chapter(title="Contents", blocks=blocks))

    for index, chapter in enumerate(chapters, start=1):
        if chapter.title == "Contents":
            heading = next((b.text for b in chapter.blocks if b.kind == "heading"), "")
            chapter.title = heading or f"Chapter {index}"
    return chapters


def _merge_adjacent_headings(blocks: list[Block]) -> list[Block]:
    merged: list[Block] = []
    for block in blocks:
        if (
            merged
            and block.kind == "heading"
            and merged[-1].kind == "heading"
            and block.level == merged[-1].level
            and len(merged[-1].text) < 80
            and len(block.text) < 80
        ):
            merged[-1].text = f"{merged[-1].text} {block.text}".strip()
            continue
        merged.append(block)
    return merged


def _clean_meta(value: object) -> str:
    if not value or not isinstance(value, str):
        return ""
    text = value.strip()
    if text.lower() in {"untitled", "unknown", "none"}:
        return ""
    return text


def _guess_title(chapters: list[Chapter], source_name: str) -> str:
    if chapters and chapters[0].title and chapters[0].title not in {"Contents", "Chapter 1"}:
        return chapters[0].title
    stem = Path(source_name).stem if source_name else ""
    return stem.replace("_", " ").replace("-", " ").strip() or "Untitled"


def _language_from_meta(meta: dict) -> str:
    raw = (meta.get("language") or meta.get("lang") or "").strip()
    if len(raw) >= 2:
        return raw[:2].lower()
    return "en"


def _normalize_spaces(text: str) -> str:
    return re.sub(r"[ \t]+", " ", text)


def document_preview(document: Document) -> dict:
    chapters = document.chapters
    first = chapters[0] if chapters else None
    return {
        "title": document.title,
        "author": document.author,
        "language": document.language,
        "page_count": document.page_count,
        "has_text": document.has_text,
        "image_count": len(document.images),
        "chapter_count": len(chapters),
        "chapters": [chapter.title for chapter in chapters],
        "preview_html": _preview_html(first) if first else "",
        "has_cover": document.cover is not None,
    }


def _preview_html(chapter: Chapter) -> str:
    parts: list[str] = []
    for block in chapter.blocks[:_PREVIEW_BLOCKS]:
        if block.kind == "heading":
            level = min(max(block.level, 1), 3)
            parts.append(f"<h{level}>{html.escape(block.text)}</h{level}>")
        elif block.kind == "paragraph" and block.text.strip():
            text = block.text.strip()
            if len(text) > _PREVIEW_PARA_CHARS:
                text = text[:_PREVIEW_PARA_CHARS].rstrip() + "…"
            parts.append(f"<p>{html.escape(text)}</p>")
        elif block.kind == "table" and block.rows:
            parts.append(_preview_table(block.rows))
        elif block.kind == "image":
            parts.append("<p>[image]</p>")
    return "\n".join(parts)


def _preview_table(rows: list[list[str]]) -> str:
    parts = ["<table>"]
    for row in rows[:8]:
        parts.append("<tr>")
        for cell in row:
            parts.append(f"<td>{html.escape(cell)}</td>")
        parts.append("</tr>")
    parts.append("</table>")
    return "".join(parts)
