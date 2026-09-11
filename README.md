# PdfToEpub

Convert a PDF into a reflowable EPUB you can read on a phone, tablet, or e-reader.

The converter extracts text in reading order, rebuilds paragraphs, detects headings for a table of contents, strips repeated headers and page numbers, and keeps figures when they are large enough to matter. Files are processed locally.

## Install

From this folder:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

Or with [pipx](https://pipx.pypa.io/), so `pdftoepub` is on your PATH:

```bash
pipx install .
# or: pipx install /path/to/PdfToEpub
```

The web UI only works while the local server is running (`pdftoepub serve`, the `.command` launcher, or the login service below). Watch-folder conversion does not need the UI.

### Login service (macOS)

Start the web UI automatically when you log in:

```bash
pdftoepub install-service
# or: ./scripts/install-service.sh
```

That writes `~/Library/LaunchAgents/com.pdftoepub.serve.plist` and loads it. The converter is then at [http://127.0.0.1:8765](http://127.0.0.1:8765). Remove it with `pdftoepub uninstall-service` (or `./scripts/uninstall-service.sh`).

### Install for yourself

A local venv or pipx install is enough. There is no Homebrew formula to publish.

## Easiest ways to convert

**Double-click** `Convert PDF to EPUB.command` (or run `pdftoepub`). That opens the web UI in your browser — pick one PDF and it downloads the EPUB. If the server is already on port 8765, the launcher just opens that URL.

**Drag and drop** PDFs onto `apps/Convert PDF to EPUB.app`. The EPUB is saved next to the PDF and revealed in Finder.

**Watch folder:** drop PDFs into `inbox/`. They are converted into `outbox/` automatically. This still works without the web UI.

```bash
pdftoepub watch
```

**Web app**

```bash
pdftoepub serve --open
```

That starts [http://127.0.0.1:8765](http://127.0.0.1:8765) and opens it in your browser.

A small sample is in `examples/river-stories.pdf` if you want to try the flow immediately.

## Command line

Running `pdftoepub` with no arguments opens the web UI. `pdftoepub menu` shows the older file/folder/watch menu.

```bash
pdftoepub book.pdf
pdftoepub folder-of-pdfs/
pdftoepub book.pdf --open
pdftoepub book.pdf --open-books
pdftoepub convert book.pdf -o book.epub --title "My Book" --author "Jane Doe"
pdftoepub preview book.pdf
pdftoepub watch ~/Desktop/ToConvert --out-dir ~/Desktop/EPUBs
```

`--open` reveals the result in Finder. `--open-books` opens the EPUB in the Books app on macOS. `--skip-existing` leaves PDFs alone if a newer EPUB is already there. `--no-images` skips embedded figures.

## What converts well

Best results:

- Text-based PDFs (exports from Word, InDesign, LaTeX, most ebooks)
- Documents with a clear heading size for chapter splits

Limitations:

- Scanned PDFs have little or no text layer. There is no OCR; pages may be kept as images, but the result will not reflow like a native ebook
- Password-protected files are rejected
- The web UI converts one PDF at a time, up to 80 MB. Files stay local
- Heading and chapter detection is font-size based and can mis-split
- Repeated headers, footers, and page numbers are stripped heuristically
- Tiny or decorative images are skipped; images can be turned off
- Multi-column pages, tables, footnotes, and magazine layouts will not keep exact page design — EPUB is reflowable
- Preview and convert each re-read the PDF; preview is not a quality guarantee

## Tests

```bash
pip install -e ".[dev]"
pytest
```
