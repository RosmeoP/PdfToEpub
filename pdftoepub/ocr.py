from __future__ import annotations

import shutil
from dataclasses import dataclass

# Optional OCR for scanned / image-only pages. Tesseract, pytesseract, and
# PyMuPDF OCR extras are never required — callers should keep the image-page
# fallback when this module reports unavailable or returns no lines.

_MIN_PAGE_CHARS = 40


@dataclass
class OcrLine:
    text: str
    bbox: tuple[float, float, float, float]
    size: float = 11.0


def ocr_is_available() -> bool:
    """True when a local Tesseract binary or pytesseract can be used."""
    return _tesseract_cmd() is not None or _pytesseract_ready()


def page_needs_ocr(text: str) -> bool:
    return len((text or "").strip()) < _MIN_PAGE_CHARS


def ocr_page(page) -> list[OcrLine]:
    """OCR a page. Returns [] if OCR is unavailable or fails."""
    if not ocr_is_available():
        return []
    lines = _ocr_with_pymupdf(page)
    if lines:
        return lines
    return _ocr_with_pytesseract(page)


def _tesseract_cmd() -> str | None:
    return shutil.which("tesseract")


def _pytesseract_ready() -> bool:
    try:
        import pytesseract  # noqa: F401
    except Exception:
        return False
    return _tesseract_cmd() is not None


def _ocr_with_pymupdf(page) -> list[OcrLine]:
    if _tesseract_cmd() is None:
        return []
    try:
        textpage = page.get_textpage_ocr(language="eng", dpi=150, full=True)
        raw = page.get_text("dict", textpage=textpage)
    except Exception:
        return []
    return _lines_from_dict(raw)


def _ocr_with_pytesseract(page) -> list[OcrLine]:
    try:
        import pytesseract
        from PIL import Image
    except Exception:
        return []
    try:
        pix = page.get_pixmap(dpi=150, alpha=False)
        image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT)
    except Exception:
        return []

    scale_x = page.rect.width / max(pix.width, 1)
    scale_y = page.rect.height / max(pix.height, 1)
    grouped: dict[tuple[int, int, int], list[int]] = {}
    n = len(data.get("text") or [])
    for index in range(n):
        try:
            if int(data["conf"][index]) < 0:
                continue
        except (TypeError, ValueError):
            continue
        text = (data["text"][index] or "").strip()
        if not text:
            continue
        key = (
            int(data.get("block_num", [0])[index] or 0),
            int(data.get("par_num", [0])[index] or 0),
            int(data.get("line_num", [0])[index] or 0),
        )
        grouped.setdefault(key, []).append(index)

    lines: list[OcrLine] = []
    for indexes in grouped.values():
        words = [data["text"][i].strip() for i in indexes if (data["text"][i] or "").strip()]
        if not words:
            continue
        left = min(int(data["left"][i]) for i in indexes)
        top = min(int(data["top"][i]) for i in indexes)
        right = max(int(data["left"][i]) + int(data["width"][i]) for i in indexes)
        bottom = max(int(data["top"][i]) + int(data["height"][i]) for i in indexes)
        lines.append(
            OcrLine(
                text=" ".join(words),
                bbox=(left * scale_x, top * scale_y, right * scale_x, bottom * scale_y),
                size=11.0,
            )
        )
    lines.sort(key=lambda line: (round(line.bbox[1], 1), round(line.bbox[0], 1)))
    return lines


def _lines_from_dict(raw: dict) -> list[OcrLine]:
    lines: list[OcrLine] = []
    for block in raw.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            parts: list[str] = []
            sizes: list[float] = []
            for span in line.get("spans") or []:
                text = (span.get("text") or "").strip()
                if not text:
                    continue
                parts.append(text)
                sizes.append(float(span.get("size") or 11.0))
            text = " ".join(parts).strip()
            if not text:
                continue
            bbox = tuple(line.get("bbox") or (0, 0, 0, 0))
            lines.append(OcrLine(text=text, bbox=bbox, size=max(sizes) if sizes else 11.0))
    return lines
