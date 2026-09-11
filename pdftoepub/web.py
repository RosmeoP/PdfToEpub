from __future__ import annotations

import html
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from pdftoepub.cache import clone_document
from pdftoepub.converter import convert_document_to_epub, extract_pdf
from pdftoepub.epub_build import default_output_path
from pdftoepub.extract import document_preview
from pdftoepub.models import ConversionError, Document
from pdftoepub.progress import ProgressFn

STATIC_DIR = Path(__file__).parent / "static"
MAX_UPLOAD_BYTES = 80 * 1024 * 1024
JOB_TTL_SECONDS = 30 * 60
MAX_CACHED_EXTRACTS = 16
PREVIEW_BLOCK_LIMIT = 12

app = FastAPI(title="PdfToEpub", version="0.1.0")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@dataclass
class Job:
    id: str
    filename: str
    status: str = "queued"
    percent: int = 0
    message: str = "Starting…"
    error: str | None = None
    payload: bytes | None = None
    local_path: Path | None = None
    created_at: float = field(default_factory=time.time)


@dataclass
class CachedExtract:
    id: str
    name: str
    document: Document
    created_at: float = field(default_factory=time.time)


class ArchiveRequest(BaseModel):
    job_ids: list[str] = Field(default_factory=list)


_jobs: dict[str, Job] = {}
_extracts: dict[str, CachedExtract] = {}
_jobs_lock = threading.Lock()
_extracts_lock = threading.Lock()


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api", response_model=None)
def api_root(request: Request) -> JSONResponse:
    return JSONResponse(
        {
            "ok": True,
            "open_books": sys.platform == "darwin" and _is_local_request(request),
        }
    )


@app.post("/api/preview", response_model=None)
async def api_preview(file: UploadFile = File(...)) -> JSONResponse:
    data, name, error = await _read_pdf(file)
    if error:
        return error
    try:
        document = _extract_for_web(data, name)
    except ConversionError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    extract_id = _store_extract(name, document)
    return JSONResponse(_preview_payload(document, extract_id))


@app.post("/api/jobs", response_model=None)
async def api_start_job(
    file: UploadFile | None = File(default=None),
    extract_id: str = Form(""),
    title: str = Form(""),
    author: str = Form(""),
    include_images: str = Form("true"),
    chapters: str = Form(""),
) -> JSONResponse:
    data, name, error = await _optional_pdf(file)
    if error:
        return error
    if not data and not extract_id.strip():
        return JSONResponse({"error": "Choose a PDF first."}, status_code=400)
    if extract_id.strip() and _get_extract(extract_id.strip()) is None and not data:
        return JSONResponse(
            {"error": "Preview expired. Choose the PDF again."},
            status_code=400,
        )
    try:
        _parse_chapters(chapters)
    except ConversionError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    _forget_old_state()
    source_name = name or _extract_name(extract_id.strip()) or "document.pdf"
    job = Job(id=uuid.uuid4().hex, filename=default_output_path(source_name).name)
    with _jobs_lock:
        _jobs[job.id] = job
    thread = threading.Thread(
        target=_run_job,
        args=(
            job.id,
            data,
            source_name,
            extract_id.strip(),
            title or None,
            author or None,
            include_images,
            chapters,
        ),
        daemon=True,
    )
    thread.start()
    return JSONResponse({"id": job.id})


@app.get("/api/jobs/{job_id}", response_model=None)
def api_job_status(job_id: str) -> JSONResponse:
    job = _get_job(job_id)
    if job is None:
        return JSONResponse({"error": "Conversion job not found."}, status_code=404)
    return JSONResponse(
        {
            "id": job.id,
            "status": job.status,
            "percent": job.percent,
            "message": job.message,
            "error": job.error,
            "filename": job.filename,
        }
    )


@app.get("/api/jobs/{job_id}/file", response_model=None)
def api_job_file(job_id: str) -> StreamingResponse | JSONResponse:
    job = _get_job(job_id)
    if job is None:
        return JSONResponse({"error": "Conversion job not found."}, status_code=404)
    if job.status == "error":
        return JSONResponse({"error": job.error or "Conversion failed."}, status_code=400)
    if job.status != "done" or job.payload is None:
        return JSONResponse({"error": "The EPUB is not ready yet."}, status_code=409)
    encoded = quote(job.filename)
    return StreamingResponse(
        io.BytesIO(job.payload),
        media_type="application/epub+zip",
        headers={
            "Content-Disposition": f"attachment; filename=\"{job.filename}\"; filename*=UTF-8''{encoded}"
        },
    )


@app.post("/api/jobs/{job_id}/open-books", response_model=None)
def api_open_books(job_id: str, request: Request) -> JSONResponse:
    if not _is_local_request(request):
        return JSONResponse(
            {"error": "Opening in Books is only available on this computer."},
            status_code=403,
        )
    if sys.platform != "darwin":
        return JSONResponse(
            {"error": "Opening in Books is only available on Mac."},
            status_code=400,
        )
    job = _get_job(job_id)
    if job is None:
        return JSONResponse({"error": "Conversion job not found."}, status_code=404)
    if job.status != "done" or job.payload is None:
        return JSONResponse({"error": "The EPUB is not ready yet."}, status_code=409)
    try:
        path = _job_local_file(job)
    except OSError as exc:
        return JSONResponse({"error": f"Could not save EPUB: {exc}"}, status_code=500)
    if not _is_safe_epub_path(path):
        return JSONResponse({"error": "Refusing to open an unexpected file."}, status_code=400)
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return JSONResponse({"ok": True})
    try:
        subprocess.run(
            ["open", "-a", "Books", str(path)],
            check=False,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return JSONResponse({"error": f"Could not open Books: {exc}"}, status_code=500)
    return JSONResponse({"ok": True})


@app.post("/api/archive", response_model=None)
def api_archive(body: ArchiveRequest) -> StreamingResponse | JSONResponse:
    files: list[tuple[str, bytes]] = []
    used: set[str] = set()
    for job_id in body.job_ids:
        job = _get_job(job_id)
        if job is None or job.status != "done" or job.payload is None:
            continue
        name = _unique_zip_name(job.filename, used)
        files.append((name, job.payload))
    if not files:
        return JSONResponse({"error": "No finished EPUBs to download."}, status_code=400)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, payload in files:
            archive.writestr(name, payload)
    buffer.seek(0)
    return StreamingResponse(
        buffer,
        media_type="application/zip",
        headers={
            "Content-Disposition": 'attachment; filename="pdftoepub-epubs.zip"'
        },
    )


@app.post("/api/convert", response_model=None)
async def api_convert(
    file: UploadFile | None = File(default=None),
    extract_id: str = Form(""),
    title: str = Form(""),
    author: str = Form(""),
    include_images: str = Form("true"),
    chapters: str = Form(""),
) -> StreamingResponse | JSONResponse:
    data, name, error = await _optional_pdf(file)
    if error:
        return error
    if not data and not extract_id.strip():
        return JSONResponse({"error": "Choose a PDF first."}, status_code=400)
    try:
        document, source_name = _document_for_convert(
            data,
            name,
            extract_id.strip(),
            title or None,
            author or None,
            include_images,
            chapters,
        )
    except ConversionError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    with tempfile.TemporaryDirectory(prefix="pdftoepub-") as tmp:
        destination = default_output_path(source_name, Path(tmp))
        try:
            written = _write_converted_document(document, destination)
            payload = written.read_bytes()
        except ConversionError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
    download_name = default_output_path(source_name).name
    encoded = quote(download_name)
    return StreamingResponse(
        io.BytesIO(payload),
        media_type="application/epub+zip",
        headers={
            "Content-Disposition": f"attachment; filename=\"{download_name}\"; filename*=UTF-8''{encoded}"
        },
    )


def _run_job(
    job_id: str,
    data: bytes,
    name: str,
    extract_id: str,
    title: str | None,
    author: str | None,
    include_images: str,
    chapters: str,
) -> None:
    job = _get_job(job_id)
    if job is None:
        return
    job.status = "running"
    job.message = "Starting conversion…"

    def on_progress(percent: int, message: str) -> None:
        current = _get_job(job_id)
        if current is None:
            return
        current.percent = percent
        current.message = message

    with tempfile.TemporaryDirectory(prefix="pdftoepub-") as tmp:
        destination = default_output_path(name, Path(tmp))
        try:
            document, source_name = _document_for_convert(
                data,
                name,
                extract_id,
                title,
                author,
                include_images,
                chapters,
                progress=on_progress,
            )
            destination = default_output_path(source_name, Path(tmp))
            written = _write_converted_document(document, destination, progress=on_progress)
            job.payload = written.read_bytes()
            job.filename = default_output_path(source_name).name
            job.percent = 100
            job.message = "Finished"
            job.status = "done"
        except ConversionError as exc:
            job.status = "error"
            job.error = str(exc)
            job.message = str(exc)
        except Exception as exc:
            job.status = "error"
            job.error = str(exc) or "Conversion failed."
            job.message = job.error


def _document_for_convert(
    data: bytes,
    name: str,
    extract_id: str,
    title: str | None,
    author: str | None,
    include_images: str,
    chapters: str,
    progress: ProgressFn | None = None,
) -> tuple[Document, str]:
    include = include_images.lower() not in {"false", "0", "no"}
    chapters_spec = _parse_chapters(chapters)
    cached = _get_extract(extract_id) if extract_id else None
    if extract_id and cached is None and not data:
        raise ConversionError("Preview expired. Choose the PDF again.")

    if cached is not None:
        if progress:
            progress(58, "Using cached preview…")
        document = cached.document
        source_name = cached.name or name or "document.pdf"
    else:
        source_name = name or "document.pdf"
        document = _extract_for_web(
            data,
            source_name,
            title=title,
            author=author,
            include_images=include,
            progress=progress,
        )
    document = _apply_structure_edits(document, title, author, chapters_spec, include)
    return document, source_name


def _extract_for_web(
    data: bytes,
    name: str,
    title: str | None = None,
    author: str | None = None,
    include_images: bool = True,
    progress: ProgressFn | None = None,
) -> Document:
    return extract_pdf(
        data,
        title=title,
        author=author,
        include_images=include_images,
        source_name=name,
        progress=progress,
        ocr=True,
        use_cache=True,
    )


def _write_converted_document(
    document: Document,
    destination: Path,
    progress: ProgressFn | None = None,
) -> Path:
    return convert_document_to_epub(document, output=destination, progress=progress)


def _preview_payload(document: Document, extract_id: str) -> dict:
    info = document_preview(document)
    if not info.get("preview_html"):
        info["preview_html"] = _first_chapter_preview_html(document)
    if not info.get("preview_paragraphs"):
        info["preview_paragraphs"] = _first_chapter_paragraphs(document)
    if not info.get("chapter_items"):
        info["chapter_items"] = [
            {"index": index, "title": chapter.title}
            for index, chapter in enumerate(document.chapters)
        ]
    info["extract_id"] = extract_id
    return info


def _first_chapter_preview_html(document: Document) -> str:
    if not document.chapters:
        return ""
    parts: list[str] = []
    count = 0
    for block in document.chapters[0].blocks:
        text = block.text.strip()
        if block.kind == "heading" and text:
            parts.append(f"<h3>{html.escape(text)}</h3>")
        elif block.kind == "paragraph" and text:
            parts.append(f"<p>{html.escape(text)}</p>")
        elif block.kind == "table" and block.rows:
            cells = "".join(
                "<tr>" + "".join(f"<td>{html.escape(cell)}</td>" for cell in row) + "</tr>"
                for row in block.rows[:8]
            )
            parts.append(f"<table>{cells}</table>")
        else:
            continue
        count += 1
        if count >= PREVIEW_BLOCK_LIMIT:
            break
    return "".join(parts)


def _first_chapter_paragraphs(document: Document) -> list[str]:
    if not document.chapters:
        return []
    texts: list[str] = []
    for block in document.chapters[0].blocks:
        if block.kind in {"heading", "paragraph"} and block.text.strip():
            texts.append(block.text.strip())
        if len(texts) >= PREVIEW_BLOCK_LIMIT:
            break
    return texts


def _apply_structure_edits(
    document: Document,
    title: str | None,
    author: str | None,
    chapters_spec: list[dict] | None,
    include_images: bool,
) -> Document:
    edited = clone_document(document)
    if chapters_spec is None:
        chapters = edited.chapters
    else:
        chapters = []
        seen: set[int] = set()
        for item in chapters_spec:
            try:
                index = int(item.get("index"))
            except (TypeError, ValueError, AttributeError):
                continue
            if index in seen or index < 0 or index >= len(edited.chapters):
                continue
            seen.add(index)
            source = edited.chapters[index]
            source.title = str(item.get("title") or source.title).strip() or source.title
            chapters.append(source)
    if not include_images:
        for chapter in chapters:
            chapter.blocks = [block for block in chapter.blocks if block.kind != "image"]
        edited.images = []
        edited.cover = None
    else:
        used = {
            block.image.uid
            for chapter in chapters
            for block in chapter.blocks
            if block.image is not None
        }
        if edited.cover is not None:
            used.add(edited.cover.uid)
        edited.images = [image for image in edited.images if image.uid in used]
    if not chapters:
        raise ConversionError("Keep at least one chapter to convert.")
    edited.title = (title or "").strip() or edited.title
    edited.author = (author or "").strip() or edited.author
    edited.chapters = chapters
    return edited


def _parse_chapters(raw: str) -> list[dict] | None:
    if not raw or not str(raw).strip():
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConversionError("Chapter edits were not valid.") from exc
    if not isinstance(data, list):
        raise ConversionError("Chapter edits were not valid.")
    return data


def _store_extract(name: str, document: Document) -> str:
    _forget_old_state()
    cached = CachedExtract(id=uuid.uuid4().hex, name=name, document=document)
    with _extracts_lock:
        _extracts[cached.id] = cached
        if len(_extracts) > MAX_CACHED_EXTRACTS:
            oldest = sorted(_extracts.values(), key=lambda item: item.created_at)
            for item in oldest[: len(_extracts) - MAX_CACHED_EXTRACTS]:
                _extracts.pop(item.id, None)
    return cached.id


def _get_extract(extract_id: str) -> CachedExtract | None:
    if not extract_id:
        return None
    with _extracts_lock:
        return _extracts.get(extract_id)


def _extract_name(extract_id: str) -> str:
    cached = _get_extract(extract_id)
    return cached.name if cached else ""


def _get_job(job_id: str) -> Job | None:
    with _jobs_lock:
        return _jobs.get(job_id)


def _forget_old_state() -> None:
    cutoff = time.time() - JOB_TTL_SECONDS
    with _jobs_lock:
        stale = [job_id for job_id, job in _jobs.items() if job.created_at < cutoff]
        for job_id in stale:
            job = _jobs.pop(job_id, None)
            if job and job.local_path and job.local_path.exists():
                try:
                    job.local_path.unlink()
                except OSError:
                    pass
    with _extracts_lock:
        stale_extracts = [
            extract_id for extract_id, item in _extracts.items() if item.created_at < cutoff
        ]
        for extract_id in stale_extracts:
            _extracts.pop(extract_id, None)


def _job_local_file(job: Job) -> Path:
    if job.local_path is not None and job.local_path.exists():
        return job.local_path
    handle = tempfile.NamedTemporaryFile(
        prefix="pdftoepub-",
        suffix=".epub",
        delete=False,
    )
    try:
        handle.write(job.payload or b"")
    finally:
        handle.close()
    path = Path(handle.name)
    job.local_path = path
    return path


def _is_safe_epub_path(path: Path) -> bool:
    try:
        resolved = path.resolve()
        tmp = Path(tempfile.gettempdir()).resolve()
    except OSError:
        return False
    return resolved.suffix.lower() == ".epub" and resolved.is_relative_to(tmp)


def _is_local_request(request: Request) -> bool:
    client_host = (request.client.host if request.client else "") or ""
    if client_host in {"127.0.0.1", "::1", "localhost", "testclient"}:
        return True
    hostname = (request.url.hostname or "").lower()
    return hostname in {"127.0.0.1", "localhost", "[::1]"}


def _unique_zip_name(filename: str, used: set[str]) -> str:
    name = Path(filename).name or "book.epub"
    if name not in used:
        used.add(name)
        return name
    stem = Path(name).stem or "book"
    index = 2
    while True:
        candidate = f"{stem}-{index}.epub"
        if candidate not in used:
            used.add(candidate)
            return candidate
        index += 1


async def _optional_pdf(file: UploadFile | None) -> tuple[bytes, str, JSONResponse | None]:
    if file is None:
        return b"", "", None
    filename = Path(file.filename or "").name
    if not filename:
        return b"", "", None
    return await _read_pdf(file)


async def _read_pdf(file: UploadFile) -> tuple[bytes, str, JSONResponse | None]:
    name = Path(file.filename or "document.pdf").name
    if not name.lower().endswith(".pdf"):
        return b"", name, JSONResponse({"error": "Please upload a PDF file."}, status_code=400)
    data = await file.read()
    if not data:
        return data, name, JSONResponse({"error": "The uploaded file is empty."}, status_code=400)
    if len(data) > MAX_UPLOAD_BYTES:
        return data, name, JSONResponse({"error": "PDF is larger than the 80 MB limit."}, status_code=413)
    if not data.startswith(b"%PDF"):
        return data, name, JSONResponse({"error": "That file does not look like a PDF."}, status_code=400)
    return data, name, None
