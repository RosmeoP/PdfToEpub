from pathlib import Path

from pdftoepub.cli import build_parser, main, normalize_argv
from pdftoepub.service import LABEL, DEFAULT_URL, render_plist
from tests.test_converter import _make_pdf


def test_normalize_argv_implicit_convert() -> None:
    assert normalize_argv(["book.pdf"]) == ["convert", "book.pdf"]
    assert normalize_argv(["book.pdf", "--open"]) == ["convert", "book.pdf", "--open"]
    assert normalize_argv(["book.pdf", "--open-books"]) == ["convert", "book.pdf", "--open-books"]
    assert normalize_argv(["install-service", "--dest", "x"]) == ["install-service", "--dest", "x"]
    assert normalize_argv(["uninstall-service"]) == ["uninstall-service"]


def test_convert_flag_parsing() -> None:
    parser = build_parser()
    books = parser.parse_args(["convert", "book.pdf", "--open-books"])
    assert books.command == "convert"
    assert books.open_books is True
    assert books.open is False

    finder = parser.parse_args(["convert", "book.pdf", "--open"])
    assert finder.open is True
    assert finder.open_books is False

    both = parser.parse_args(["convert", "book.pdf", "--open", "--open-books"])
    assert both.open is True
    assert both.open_books is True


def test_install_service_flag_parsing() -> None:
    parser = build_parser()
    args = parser.parse_args(["install-service", "--dest", "/tmp/agents", "--no-load"])
    assert args.command == "install-service"
    assert args.dest == Path("/tmp/agents")
    assert args.no_load is True

    uninstall = parser.parse_args(["uninstall-service", "--dest", "/tmp/agents", "--no-load"])
    assert uninstall.command == "uninstall-service"
    assert uninstall.no_load is True


def test_install_service_writes_plist(tmp_path: Path, capsys) -> None:
    dest = tmp_path / "LaunchAgents"
    assert main(["install-service", "--dest", str(dest), "--no-load"]) == 0
    plist = dest / "com.pdftoepub.serve.plist"
    assert plist.is_file()
    text = plist.read_text(encoding="utf-8")
    assert LABEL in text
    assert "serve" in text
    assert "127.0.0.1" in text
    assert "8765" in text
    out = capsys.readouterr().out
    assert str(plist) in out
    assert DEFAULT_URL in out

    assert main(["install-service", "--dest", str(dest), "--no-load"]) == 0
    assert main(["uninstall-service", "--dest", str(dest), "--no-load"]) == 0
    assert not plist.exists()
    assert main(["uninstall-service", "--dest", str(dest), "--no-load"]) == 0


def test_render_plist_uses_requested_program() -> None:
    text = render_plist(
        program_arguments=["/tmp/venv/bin/python", "-m", "pdftoepub", "serve", "--host", "127.0.0.1", "--port", "8765"],
        workdir=Path("/tmp/PdfToEpub"),
        log_dir=Path("/tmp/logs"),
    )
    assert "<string>/tmp/venv/bin/python</string>" in text
    assert "<string>-m</string>" in text
    assert "<string>pdftoepub</string>" in text
    assert "<string>serve</string>" in text
    assert "<string>127.0.0.1</string>" in text
    assert "<string>8765</string>" in text
    assert "<string>/tmp/PdfToEpub</string>" in text


def test_open_books_invokes_picker(tmp_path: Path, monkeypatch) -> None:
    pdf = _make_pdf(tmp_path / "book.pdf")
    opened: list[Path] = []
    revealed: list[Path] = []
    monkeypatch.setattr("pdftoepub.cli.open_in_books", opened.append)
    monkeypatch.setattr("pdftoepub.cli.reveal", revealed.append)
    assert main(["convert", str(pdf), "--open-books", "-o", str(tmp_path / "book.epub")]) == 0
    assert opened and opened[0].name == "book.epub"
    assert revealed == []


def test_open_reveals_in_finder(tmp_path: Path, monkeypatch) -> None:
    pdf = _make_pdf(tmp_path / "book.pdf")
    revealed: list[Path] = []
    monkeypatch.setattr("pdftoepub.cli.reveal", revealed.append)
    monkeypatch.setattr("pdftoepub.cli.open_in_books", lambda path: None)
    assert main(["convert", str(pdf), "--open", "-o", str(tmp_path / "book.epub")]) == 0
    assert revealed and revealed[0].name == "book.epub"
