from __future__ import annotations

import io
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from pdftoepub.converter import convert_pdf_to_epub, preview_pdf
from pdftoepub.epub_build import default_output_path
from pdftoepub.models import ConversionError

STATIC_DIR = Path(__file__).parent / "static"
MAX_UPLOAD_BYTES = 80 * 1024 * 1024
JOB_TTL_SECONDS = 30 * 60

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
    created_at: float = field(default_factory=time.time)


_jobs: dict[str, Job] = {}
_jobs_lock = threading.Lock()


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.post("/api/preview", response_model=None)
async def api_preview(file: UploadFile = File(...)) -> JSONResponse:
    data, name, error = await _read_pdf(file)
    if error:
        return error
    try:
        info = preview_pdf(data, source_name=name)
    except ConversionError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return JSONResponse(info)


@app.post("/api/jobs", response_model=None)
async def api_start_job(
    file: UploadFile = File(...),
    title: str = Form(""),
    author: str = Form(""),
    include_images: str = Form("true"),
) -> JSONResponse:
    data, name, error = await _read_pdf(file)
    if error:
        return error
    _forget_old_jobs()
    job = Job(id=uuid.uuid4().hex, filename=default_output_path(name).name)
    with _jobs_lock:
        _jobs[job.id] = job
    thread = threading.Thread(
        target=_run_job,
        args=(job.id, data, name, title or None, author or None, include_images),
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


@app.post("/api/convert", response_model=None)
async def api_convert(
    file: UploadFile = File(...),
    title: str = Form(""),
    author: str = Form(""),
    include_images: str = Form("true"),
) -> StreamingResponse | JSONResponse:
    data, name, error = await _read_pdf(file)
    if error:
        return error
    with tempfile.TemporaryDirectory(prefix="pdftoepub-") as tmp:
        destination = default_output_path(name, Path(tmp))
        try:
            written = convert_pdf_to_epub(
                data,
                output=destination,
                title=title or None,
                author=author or None,
                include_images=include_images.lower() not in {"false", "0", "no"},
                source_name=name,
            )
            payload = written.read_bytes()
        except ConversionError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
    download_name = default_output_path(name).name
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
    title: str | None,
    author: str | None,
    include_images: str,
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
            written = convert_pdf_to_epub(
                data,
                output=destination,
                title=title,
                author=author,
                include_images=include_images.lower() not in {"false", "0", "no"},
                source_name=name,
                progress=on_progress,
            )
            job.payload = written.read_bytes()
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


def _get_job(job_id: str) -> Job | None:
    with _jobs_lock:
        return _jobs.get(job_id)


def _forget_old_jobs() -> None:
    cutoff = time.time() - JOB_TTL_SECONDS
    with _jobs_lock:
        stale = [job_id for job_id, job in _jobs.items() if job.created_at < cutoff]
        for job_id in stale:
            _jobs.pop(job_id, None)


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
