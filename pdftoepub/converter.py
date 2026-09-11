from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pdftoepub.cache import clear_extract_cache, extract_cache_stats
from pdftoepub.epub_build import default_output_path, write_epub
from pdftoepub.extract import document_preview, extract_document
from pdftoepub.models import Document
from pdftoepub.ocr import ocr_is_available
from pdftoepub.progress import ProgressFn

__all__ = [
    "ConvertOptions",
    "clear_extract_cache",
    "convert_document_to_epub",
    "convert_pdf_to_epub",
    "document_preview",
    "extract_cache_stats",
    "extract_pdf",
    "ocr_is_available",
    "preview_pdf",
]


@dataclass
class ConvertOptions:
    title: str | None = None
    author: str | None = None
    include_images: bool = True
    output: Path | None = None
    ocr: bool = True
    use_cache: bool = True


def convert_pdf_to_epub(
    source: Path | str | bytes,
    *,
    output: Path | str | None = None,
    title: str | None = None,
    author: str | None = None,
    include_images: bool = True,
    source_name: str = "",
    progress: ProgressFn | None = None,
    ocr: bool = True,
    use_cache: bool = True,
) -> Path:
    document = extract_pdf(
        source,
        title=title,
        author=author,
        include_images=include_images,
        source_name=source_name,
        progress=progress,
        ocr=ocr,
        use_cache=use_cache,
    )
    if output is None:
        name = source_name or (str(source) if not isinstance(source, bytes) else "document.pdf")
        destination = default_output_path(name)
    else:
        destination = Path(output)
    return write_epub(document, destination, progress=progress)


def convert_document_to_epub(
    document: Document,
    *,
    output: Path | str,
    progress: ProgressFn | None = None,
) -> Path:
    """Write an EPUB from an already-extracted Document (no second PDF parse)."""
    return write_epub(document, Path(output), progress=progress)


def extract_pdf(
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
    if isinstance(source, bytes) and not source_name:
        source_name = "document.pdf"
    elif not source_name and not isinstance(source, bytes):
        source_name = Path(source).name
    return extract_document(
        source,
        title=title,
        author=author,
        include_images=include_images,
        source_name=source_name,
        progress=progress,
        ocr=ocr,
        use_cache=use_cache,
    )


def preview_pdf(
    source: Path | str | bytes,
    *,
    title: str | None = None,
    author: str | None = None,
    include_images: bool = True,
    source_name: str = "",
    ocr: bool = True,
    use_cache: bool = True,
) -> dict:
    document = extract_pdf(
        source,
        title=title,
        author=author,
        include_images=include_images,
        source_name=source_name,
        ocr=ocr,
        use_cache=use_cache,
    )
    return document_preview(document)
