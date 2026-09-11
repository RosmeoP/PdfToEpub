from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from pdftoepub.jobs import (
    DEFAULT_INBOX,
    DEFAULT_OUTBOX,
    JobResult,
    collect_pdfs,
    convert_many,
)
from pdftoepub.models import ConversionError
from pdftoepub.picker import notify, open_in_books, pick_folder, pick_pdfs, reveal
from pdftoepub.service import DEFAULT_URL, install_service, uninstall_service

COMMANDS = {"convert", "preview", "serve", "watch", "menu", "install-service", "uninstall-service"}
IMPLICIT_CONVERT_FLAGS = {
    "-o",
    "--output",
    "--out-dir",
    "--title",
    "--author",
    "--no-images",
    "--open",
    "--open-books",
    "--skip-existing",
}


def normalize_argv(argv: list[str]) -> list[str]:
    if not argv:
        return argv
    if argv[0] not in COMMANDS and (not argv[0].startswith("-") or argv[0] in IMPLICIT_CONVERT_FLAGS):
        return ["convert", *argv]
    return argv


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pdftoepub",
        description="Convert PDFs into EPUB books. Run with no arguments for a simple menu.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    convert = sub.add_parser("convert", help="Convert one or more PDFs, or a folder")
    convert.add_argument("paths", nargs="*", type=Path, help="PDF files or folders")
    _add_convert_flags(convert)

    preview = sub.add_parser("preview", help="Show detected title, author, and chapters")
    preview.add_argument("pdf", type=Path, help="Path to the PDF file")

    watch = sub.add_parser("watch", help="Auto-convert new PDFs dropped into a folder")
    watch.add_argument("folder", nargs="?", type=Path, help="Folder to watch (default: ./inbox)")
    _add_convert_flags(watch)

    serve = sub.add_parser("serve", help="Start the local web converter")
    serve.add_argument("--host", default="127.0.0.1", help="Bind host (default: 127.0.0.1)")
    serve.add_argument("--port", type=int, default=8765, help="Bind port (default: 8765)")
    serve.add_argument("--open", action="store_true", help="Open the app in your browser")

    sub.add_parser("menu", help="Interactive menu for files, folders, and watch mode")

    install = sub.add_parser("install-service", help="Install a login LaunchAgent for the web UI (macOS)")
    install.add_argument("--dest", type=Path, help="Directory or plist path (default: ~/Library/LaunchAgents)")
    install.add_argument("--no-load", action="store_true", help="Write the plist without calling launchctl")

    uninstall = sub.add_parser("uninstall-service", help="Remove the login LaunchAgent (macOS)")
    uninstall.add_argument("--dest", type=Path, help="Directory or plist path (default: ~/Library/LaunchAgents)")
    uninstall.add_argument("--no-load", action="store_true", help="Remove the plist without calling launchctl")
    return parser


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        return _serve("127.0.0.1", 8765, open_browser=True)
    argv = normalize_argv(argv)
    args = build_parser().parse_args(argv)

    if args.command == "menu":
        return _interactive()
    if args.command == "serve":
        return _serve(args.host, args.port, args.open)
    if args.command == "install-service":
        return _install_service(args)
    if args.command == "uninstall-service":
        return _uninstall_service(args)
    if args.command == "preview":
        return _preview(args.pdf)
    if args.command == "watch":
        return _watch(
            args.folder,
            args.output,
            args.out_dir,
            args.title,
            args.author,
            not args.no_images,
            args.open,
            args.skip_existing,
            open_books=args.open_books,
        )
    return _convert_command(args)


def _add_convert_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("-o", "--output", type=Path, help="Output .epub path (single file) or folder")
    parser.add_argument("--out-dir", type=Path, help="Folder for converted EPUBs")
    parser.add_argument("--title", help="Override the book title")
    parser.add_argument("--author", help="Override the author name")
    parser.add_argument("--no-images", action="store_true", help="Skip embedded images")
    parser.add_argument("--open", action="store_true", help="Reveal the result in Finder")
    parser.add_argument("--open-books", action="store_true", help="Open the EPUB in Books (macOS)")
    parser.add_argument("--skip-existing", action="store_true", help="Skip PDFs that already have a newer EPUB")


def _install_service(args: argparse.Namespace) -> int:
    if sys.platform != "darwin" and not args.no_load:
        print("install-service is only supported on macOS.", file=sys.stderr)
        return 1
    path = install_service(dest=args.dest, load=not args.no_load)
    print(f"LaunchAgent: {path}")
    print(f"URL: {DEFAULT_URL}")
    return 0


def _uninstall_service(args: argparse.Namespace) -> int:
    if sys.platform != "darwin" and not args.no_load:
        print("uninstall-service is only supported on macOS.", file=sys.stderr)
        return 1
    path = uninstall_service(dest=args.dest, load=not args.no_load)
    if path is None:
        print("LaunchAgent is not installed.")
    else:
        print(f"Removed LaunchAgent: {path}")
    return 0


def _interactive() -> int:
    if not sys.stdin.isatty():
        print("Usage: pdftoepub [file-or-folder]    pdftoepub watch    pdftoepub serve", file=sys.stderr)
        return 1
    print("PdfToEpub")
    print("  1) Choose PDF files")
    print("  2) Convert a whole folder")
    print("  3) Watch a folder (auto-convert new PDFs)")
    print("  4) Open the web app")
    choice = input("Choose [1]: ").strip() or "1"
    if choice == "1":
        paths = pick_pdfs()
        if not paths:
            print("No file selected.")
            return 0
        return _run_jobs(paths, open_result=True)
    if choice == "2":
        folder = pick_folder("Choose a folder of PDFs")
        if folder is None:
            print("No folder selected.")
            return 0
        return _run_jobs([folder], skip_existing=True, open_result=True)
    if choice == "3":
        folder = pick_folder("Choose a folder to watch")
        if folder is None:
            print(f"Using {DEFAULT_INBOX.resolve()}")
            folder = DEFAULT_INBOX
        return _watch(folder, None, None, None, None, True, True, True)
    if choice == "4":
        return _serve("127.0.0.1", 8765, open_browser=True)
    print("Unknown choice.")
    return 1


def _convert_command(args: argparse.Namespace) -> int:
    paths = list(args.paths)
    if not paths:
        if not sys.stdin.isatty():
            print("Pass a PDF, a folder, or run this in a terminal to pick files.", file=sys.stderr)
            return 1
        paths = pick_pdfs()
        if not paths:
            print("No file selected.")
            return 0
    return _run_jobs(
        paths,
        output=args.output,
        out_dir=args.out_dir,
        title=args.title,
        author=args.author,
        include_images=not args.no_images,
        skip_existing=args.skip_existing,
        open_result=args.open,
        open_books=args.open_books,
    )


def _run_jobs(
    paths: list[Path],
    *,
    output: Path | None = None,
    out_dir: Path | None = None,
    title: str | None = None,
    author: str | None = None,
    include_images: bool = True,
    skip_existing: bool = False,
    open_result: bool = False,
    open_books: bool = False,
) -> int:
    pdfs, errors = collect_pdfs(paths)
    for message in errors:
        print(message, file=sys.stderr)
    if not pdfs:
        return 2
    try:
        results = convert_many(
            pdfs,
            output=output,
            out_dir=out_dir,
            title=title,
            author=author,
            include_images=include_images,
            skip_existing=skip_existing,
            progress=_cli_progress if sys.stderr.isatty() else None,
        )
    except ConversionError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return _report(results, open_result=open_result, open_books=open_books)


def _cli_progress(percent: int, message: str) -> None:
    width = 24
    filled = int(width * percent / 100)
    bar = "█" * filled + "░" * (width - filled)
    print(f"\r{bar} {percent:3d}%  {message:<48}", end="", file=sys.stderr, flush=True)
    if percent >= 100:
        print(file=sys.stderr)


def _report(results: list[JobResult], *, open_result: bool, open_books: bool = False) -> int:
    written: list[Path] = []
    failed = 0
    for index, result in enumerate(results, start=1):
        prefix = f"[{index}/{len(results)}] {result.source.name}"
        if result.skipped:
            print(f"{prefix}: already converted → {result.output}")
            continue
        if result.error:
            print(f"{prefix}: {result.error}", file=sys.stderr)
            failed += 1
            continue
        print(f"{prefix}: wrote {result.output}")
        if result.output is not None:
            written.append(result.output)
    if open_result and written:
        reveal(written[-1])
    if open_books and written:
        open_in_books(written[-1])
    if written:
        notify("PdfToEpub", f"Converted {len(written)} file{'s' if len(written) != 1 else ''}.")
    if failed:
        return 1
    return 0 if written or any(result.skipped for result in results) else 1


def _preview(pdf: Path) -> int:
    from pdftoepub.converter import preview_pdf

    if not pdf.exists():
        print(f"File not found: {pdf}", file=sys.stderr)
        return 2
    try:
        info = preview_pdf(pdf, source_name=pdf.name)
    except ConversionError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"Title:    {info['title']}")
    print(f"Author:   {info['author']}")
    print(f"Pages:    {info['page_count']}")
    print(f"Chapters: {info['chapter_count']}")
    print(f"Images:   {info['image_count']}")
    if not info["has_text"]:
        print("Warning: little or no extractable text (possibly a scanned PDF).")
    for index, name in enumerate(info["chapters"], start=1):
        print(f"  {index}. {name}")
    return 0


def _watch(
    folder: Path | None,
    output: Path | None,
    out_dir: Path | None,
    title: str | None,
    author: str | None,
    include_images: bool,
    open_result: bool,
    skip_existing: bool,
    *,
    open_books: bool = False,
) -> int:
    watch_dir = Path(folder) if folder is not None else DEFAULT_INBOX
    watch_dir.mkdir(parents=True, exist_ok=True)
    destination_dir = out_dir or output
    if destination_dir is None and folder is None:
        destination_dir = DEFAULT_OUTBOX
    if destination_dir is not None:
        Path(destination_dir).mkdir(parents=True, exist_ok=True)
    print(f"Watching {watch_dir.resolve()}")
    if destination_dir is not None:
        print(f"EPUBs will be saved to {Path(destination_dir).resolve()}")
    else:
        print("EPUBs will be saved next to each PDF.")
    print("Drop PDF files into that folder. Press Ctrl+C to stop.")
    seen: dict[Path, tuple[int, int]] = {}
    ready: set[Path] = set()
    try:
        while True:
            pdfs, _ = collect_pdfs([watch_dir])
            current = set(pdfs)
            for pdf in pdfs:
                stamp = _stamp(pdf)
                if seen.get(pdf) == stamp:
                    if pdf not in ready:
                        ready.add(pdf)
                        _run_jobs(
                            [pdf],
                            out_dir=destination_dir,
                            title=title,
                            author=author,
                            include_images=include_images,
                            skip_existing=True,
                            open_result=open_result,
                            open_books=open_books,
                        )
                else:
                    seen[pdf] = stamp
                    ready.discard(pdf)
            for pdf in list(seen):
                if pdf not in current:
                    seen.pop(pdf, None)
                    ready.discard(pdf)
            time.sleep(1.5)
    except KeyboardInterrupt:
        print("\nStopped watching.")
        return 0


def _stamp(path: Path) -> tuple[int, int]:
    stat = path.stat()
    return stat.st_size, stat.st_mtime_ns


def _serve(host: str, port: int, open_browser: bool = False) -> int:
    import uvicorn

    from pdftoepub.web import app

    if open_browser:
        _open_browser(host, port)
    uvicorn.run(app, host=host, port=port, log_level="info")
    return 0


def _open_browser(host: str, port: int) -> None:
    import threading
    import webbrowser

    url = f"http://{host}:{port}"

    def launch() -> None:
        time.sleep(0.6)
        webbrowser.open(url)

    threading.Thread(target=launch, daemon=True).start()


if __name__ == "__main__":
    raise SystemExit(main())
