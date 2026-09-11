import io
import json
import sys
import time
import zipfile
from pathlib import Path

import pymupdf
from fastapi.testclient import TestClient

from pdftoepub.web import app
from tests.test_converter import _make_pdf


def _wait_for_job(client: TestClient, job_id: str) -> dict:
    job = None
    for _ in range(50):
        status = client.get(f"/api/jobs/{job_id}")
        assert status.status_code == 200
        job = status.json()
        assert 0 <= job["percent"] <= 100
        if job["status"] in {"done", "error"}:
            break
        time.sleep(0.05)
    assert job is not None
    return job


def _make_markup_pdf(path: Path) -> Path:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 80), "Safe Preview", fontsize=22)
    page.insert_text(
        (72, 140),
        "Hello <script>alert(1)</script> world",
        fontsize=11,
    )
    doc.set_metadata({"title": "Safe Preview", "author": "Tester"})
    doc.save(path)
    doc.close()
    return path


def test_index_renders() -> None:
    client = TestClient(app)
    response = client.get("/")
    assert response.status_code == 200
    assert "PdfToEpub" in response.text
    assert "Choose a PDF" in response.text
    assert "progress" in response.text
    assert "Limitations" in response.text
    assert "80 MB" in response.text
    assert "Password-protected" in response.text
    assert "OCR runs if Tesseract" in response.text
    assert 'multiple' in response.text
    assert "offline-banner" in response.text
    assert "The converter is not running" in response.text
    assert "pdftoepub serve --open" in response.text
    assert "Convert PDF to EPUB.command" in response.text
    assert "chapter-list" in response.text
    assert "preview-text" in response.text
    assert "Open in Books" in response.text
    assert 'id="images-input"' in response.text


def test_api_health() -> None:
    client = TestClient(app)
    response = client.get("/api")
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert "open_books" in body


def test_preview_and_convert_api(tmp_path: Path) -> None:
    pdf = _make_pdf(tmp_path / "river.pdf")
    client = TestClient(app)
    preview = client.post(
        "/api/preview",
        files={"file": ("river.pdf", pdf.read_bytes(), "application/pdf")},
    )
    assert preview.status_code == 200
    body = preview.json()
    assert body["title"] == "River Stories"
    assert body["page_count"] == 2

    converted = client.post(
        "/api/convert",
        files={"file": ("river.pdf", pdf.read_bytes(), "application/pdf")},
        data={"title": "River Stories", "author": "Ada Ferry", "include_images": "true"},
    )
    assert converted.status_code == 200
    assert converted.headers["content-type"].startswith("application/epub+zip")
    assert converted.content[:2] == b"PK"


def test_convert_job_reports_progress(tmp_path: Path) -> None:
    pdf = _make_pdf(tmp_path / "river.pdf")
    client = TestClient(app)
    started = client.post(
        "/api/jobs",
        files={"file": ("river.pdf", pdf.read_bytes(), "application/pdf")},
        data={"title": "River Stories", "author": "Ada Ferry", "include_images": "true"},
    )
    assert started.status_code == 200
    job_id = started.json()["id"]
    job = _wait_for_job(client, job_id)
    assert job["status"] == "done"
    assert job["percent"] == 100
    downloaded = client.get(f"/api/jobs/{job_id}/file")
    assert downloaded.status_code == 200
    assert downloaded.content[:2] == b"PK"


def test_rejects_non_pdf() -> None:
    client = TestClient(app)
    response = client.post(
        "/api/preview",
        files={"file": ("notes.txt", b"hello", "text/plain")},
    )
    assert response.status_code == 400
    assert "PDF" in response.json()["error"]


def test_rejects_empty_pdf() -> None:
    client = TestClient(app)
    response = client.post(
        "/api/preview",
        files={"file": ("blank.pdf", b"", "application/pdf")},
    )
    assert response.status_code == 400
    assert "empty" in response.json()["error"].lower()


def test_rejects_fake_pdf_bytes() -> None:
    client = TestClient(app)
    response = client.post(
        "/api/preview",
        files={"file": ("fake.pdf", b"not a pdf", "application/pdf")},
    )
    assert response.status_code == 400
    assert "look like a PDF" in response.json()["error"]


def test_preview_includes_html_and_extract_id(tmp_path: Path) -> None:
    pdf = _make_markup_pdf(tmp_path / "markup.pdf")
    client = TestClient(app)
    preview = client.post(
        "/api/preview",
        files={"file": ("markup.pdf", pdf.read_bytes(), "application/pdf")},
    )
    assert preview.status_code == 200
    body = preview.json()
    assert body["extract_id"]
    assert body["preview_html"]
    assert "&lt;script&gt;" in body["preview_html"]
    assert "<script>" not in body["preview_html"]
    assert body["preview_paragraphs"]
    assert body["chapter_items"]
    assert body["chapter_items"][0]["title"]


def test_convert_from_extract_uses_edits(tmp_path: Path) -> None:
    pdf = _make_pdf(tmp_path / "river.pdf")
    client = TestClient(app)
    preview = client.post(
        "/api/preview",
        files={"file": ("river.pdf", pdf.read_bytes(), "application/pdf")},
    )
    assert preview.status_code == 200
    info = preview.json()
    first = info["chapter_items"][0]
    edited = [{"index": first["index"], "title": "Edited Chapter"}]
    converted = client.post(
        "/api/convert",
        data={
            "extract_id": info["extract_id"],
            "title": "New Title",
            "author": "New Author",
            "include_images": "true",
            "chapters": json.dumps(edited),
        },
    )
    assert converted.status_code == 200
    assert converted.content[:2] == b"PK"
    assert "New Title.epub" in converted.headers["content-disposition"]
    with zipfile.ZipFile(io.BytesIO(converted.content)) as archive:
        names = archive.namelist()
        opf = next(name for name in names if name.endswith(".opf"))
        opf_text = archive.read(opf).decode("utf-8")
        chapter_files = [name for name in names if "chap_" in name]
        chapter_html = "\n".join(archive.read(name).decode("utf-8") for name in chapter_files)
    assert "New Title" in opf_text
    assert "Edited Chapter" in chapter_html
    if info["chapter_count"] >= 2:
        assert len(chapter_files) == 1
        assert "By noon the current" not in chapter_html


def test_edited_title_sets_download_filename(tmp_path: Path) -> None:
    pdf = _make_pdf(tmp_path / "river-stories.pdf")
    client = TestClient(app)
    preview = client.post(
        "/api/preview",
        files={"file": ("river-stories.pdf", pdf.read_bytes(), "application/pdf")},
    )
    assert preview.status_code == 200
    extract_id = preview.json()["extract_id"]

    started = client.post(
        "/api/jobs",
        data={
            "extract_id": extract_id,
            "title": "Custom River Book",
            "author": "Ada Ferry",
            "include_images": "true",
        },
    )
    assert started.status_code == 200
    job_id = started.json()["id"]
    job = _wait_for_job(client, job_id)
    assert job["status"] == "done"
    assert job["filename"] == "Custom River Book.epub"
    assert job["filename"] != "river-stories.epub"

    downloaded = client.get(f"/api/jobs/{job_id}/file")
    assert downloaded.status_code == 200
    disposition = downloaded.headers["content-disposition"]
    assert "Custom River Book.epub" in disposition
    assert "river-stories.epub" not in disposition
    with zipfile.ZipFile(io.BytesIO(downloaded.content)) as archive:
        opf = next(name for name in archive.namelist() if name.endswith(".opf"))
        assert "Custom River Book" in archive.read(opf).decode("utf-8")


def test_jobs_from_extract_and_open_books(tmp_path: Path) -> None:
    pdf = _make_pdf(tmp_path / "river.pdf")
    client = TestClient(app)
    preview = client.post(
        "/api/preview",
        files={"file": ("river.pdf", pdf.read_bytes(), "application/pdf")},
    )
    extract_id = preview.json()["extract_id"]
    started = client.post(
        "/api/jobs",
        data={
            "extract_id": extract_id,
            "title": "River Stories",
            "author": "Ada Ferry",
            "include_images": "true",
        },
    )
    assert started.status_code == 200
    job_id = started.json()["id"]
    job = _wait_for_job(client, job_id)
    assert job["status"] == "done"

    missing = client.post("/api/jobs/missing/open-books")
    assert missing.status_code == 404

    opened = client.post(f"/api/jobs/{job_id}/open-books")
    if sys.platform == "darwin":
        assert opened.status_code == 200
        assert opened.json()["ok"] is True
    else:
        assert opened.status_code == 400
        assert "Mac" in opened.json()["error"]


def test_archive_zip_of_finished_jobs(tmp_path: Path) -> None:
    pdf = _make_pdf(tmp_path / "river.pdf")
    client = TestClient(app)
    payload = pdf.read_bytes()
    ids: list[str] = []
    for _ in range(2):
        started = client.post(
            "/api/jobs",
            files={"file": ("river.pdf", payload, "application/pdf")},
            data={"title": "River Stories", "author": "Ada Ferry", "include_images": "true"},
        )
        job_id = started.json()["id"]
        job = _wait_for_job(client, job_id)
        assert job["status"] == "done"
        ids.append(job_id)
    archived = client.post("/api/archive", json={"job_ids": ids})
    assert archived.status_code == 200
    assert archived.headers["content-type"].startswith("application/zip")
    assert archived.content[:2] == b"PK"
    with zipfile.ZipFile(io.BytesIO(archived.content)) as archive:
        assert len(archive.namelist()) == 2


def test_convert_requires_pdf_or_extract() -> None:
    client = TestClient(app)
    response = client.post("/api/convert", data={"title": "Nope"})
    assert response.status_code == 400
    assert "PDF" in response.json()["error"]


def test_expired_extract_is_rejected() -> None:
    client = TestClient(app)
    response = client.post(
        "/api/jobs",
        data={"extract_id": "does-not-exist", "title": "Missing"},
    )
    assert response.status_code == 400
    assert "Preview expired" in response.json()["error"]
