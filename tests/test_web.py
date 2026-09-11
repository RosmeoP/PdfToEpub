import time
from pathlib import Path

from fastapi.testclient import TestClient

from pdftoepub.web import app
from tests.test_converter import _make_pdf


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
    assert "no OCR" in response.text


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
