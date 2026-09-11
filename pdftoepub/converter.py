from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pdftoepub.epub_build import default_output_path, write_epub
from pdftoepub.extract import document_preview, extract_document
from pdftoepub.models import Document
from pdftoepub.progress import ProgressFn


@dataclass
class ConvertOptions:
    title: str | None = None
    author: str | None = None
    include_images: bool = True
    output: Path | None = None


def convert_pdf_to_epub(
    source: Path | str | bytes,
    *,
    output: Path | str | None = None,
    title: str | None = None,
    author: str | None = None,
    include_images: bool = True,
    source_name: str = "",
    progress: ProgressFn | None = None,
) -> Path:
    document = extract_pdf(
        source,
        title=title,
        author=author,
        include_images=include_images,
        source_name=source_name,
        progress=progress,
    )
    if output is None:
        name = source_name or (str(source) if not isinstance(source, bytes) else "document.pdf")
        destination = default_output_path(name)
    else:
        destination = Path(output)
    return write_epub(document, destination, progress=progress)


def extract_pdf(
    source: Path | str | bytes,
    *,
    title: str | None = None,
    author: str | None = None,
    include_images: bool = True,
    source_name: str = "",
    progress: ProgressFn | None = None,
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
    )


def preview_pdf(
    source: Path | str | bytes,
    *,
    title: str | None = None,
    author: str | None = None,
    include_images: bool = True,
    source_name: str = "",
) -> dict:
    document = extract_pdf(
        source,
        title=title,
        author=author,
        include_images=include_images,
        source_name=source_name,
    )
    return document_preview(document)
