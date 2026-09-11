from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pdftoepub.converter import convert_pdf_to_epub
from pdftoepub.epub_build import default_output_path
from pdftoepub.models import ConversionError
from pdftoepub.progress import ProgressFn

DEFAULT_INBOX = Path("inbox")
DEFAULT_OUTBOX = Path("outbox")


@dataclass
class JobResult:
    source: Path
    output: Path | None = None
    error: str | None = None
    skipped: bool = False


def collect_pdfs(inputs: list[Path]) -> tuple[list[Path], list[str]]:
    pdfs: list[Path] = []
    errors: list[str] = []
    for raw in inputs:
        path = Path(raw).expanduser()
        if path.is_dir():
            found = sorted(p for p in path.iterdir() if _is_pdf(p))
            if not found:
                errors.append(f"No PDF files in {path}")
            pdfs.extend(found)
            continue
        if not path.exists():
            errors.append(f"File not found: {path}")
            continue
        if not _is_pdf(path):
            errors.append(f"Not a PDF: {path}")
            continue
        pdfs.append(path)
    # Preserve order but drop duplicates
    unique: list[Path] = []
    seen: set[Path] = set()
    for pdf in pdfs:
        resolved = pdf.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        unique.append(pdf)
    return unique, errors


def destination_for(pdf: Path, output: Path | None, out_dir: Path | None) -> Path:
    if output is not None:
        output = Path(output)
        if output.exists() and output.is_dir():
            return default_output_path(pdf.name, output)
        return output
    if out_dir is not None:
        Path(out_dir).mkdir(parents=True, exist_ok=True)
        return default_output_path(pdf.name, Path(out_dir))
    return default_output_path(pdf.name, pdf.parent)


def should_skip(pdf: Path, destination: Path, skip_existing: bool) -> bool:
    if not skip_existing or not destination.exists():
        return False
    return destination.stat().st_mtime >= pdf.stat().st_mtime


def convert_one(
    pdf: Path,
    *,
    output: Path | None = None,
    out_dir: Path | None = None,
    title: str | None = None,
    author: str | None = None,
    include_images: bool = True,
    skip_existing: bool = False,
    progress: ProgressFn | None = None,
) -> JobResult:
    destination = destination_for(pdf, output, out_dir)
    if should_skip(pdf, destination, skip_existing):
        return JobResult(source=pdf, output=destination, skipped=True)
    try:
        written = convert_pdf_to_epub(
            pdf,
            output=destination,
            title=title,
            author=author,
            include_images=include_images,
            source_name=pdf.name,
            progress=progress,
        )
    except ConversionError as exc:
        return JobResult(source=pdf, error=str(exc))
    return JobResult(source=pdf, output=written)


def convert_many(
    pdfs: list[Path],
    *,
    output: Path | None = None,
    out_dir: Path | None = None,
    title: str | None = None,
    author: str | None = None,
    include_images: bool = True,
    skip_existing: bool = False,
    progress: ProgressFn | None = None,
) -> list[JobResult]:
    single_output: Path | None = None
    if output is not None:
        output = Path(output)
        treat_as_dir = output.is_dir() or (len(pdfs) > 1 and output.suffix.lower() != ".epub")
        if treat_as_dir:
            output.mkdir(parents=True, exist_ok=True)
            out_dir = output
        elif len(pdfs) > 1:
            raise ConversionError("Use --out-dir when converting more than one PDF.")
        else:
            single_output = output
    results: list[JobResult] = []
    for index, pdf in enumerate(pdfs):
        results.append(
            convert_one(
                pdf,
                output=single_output,
                out_dir=out_dir,
                title=title,
                author=author,
                include_images=include_images,
                skip_existing=skip_existing,
                progress=_scale_progress(progress, index, len(pdfs)) if progress else None,
            )
        )
    return results


def _scale_progress(progress: ProgressFn, index: int, total: int) -> ProgressFn:
    def inner(percent: int, message: str) -> None:
        if total <= 1:
            progress(percent, message)
            return
        start = int(index / total * 100)
        span = max(1, int(100 / total))
        progress(start + int(percent / 100 * span), f"{index + 1}/{total} {message}")

    return inner


def _is_pdf(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() == ".pdf"
