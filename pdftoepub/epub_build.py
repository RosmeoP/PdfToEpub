from __future__ import annotations

import html
import re
import uuid
from pathlib import Path

from ebooklib import epub

from pdftoepub.models import Block, Chapter, Document
from pdftoepub.progress import ProgressFn, report

_STYLE = """
@namespace epub "http://www.idpf.org/2007/ops";
html, body {
  margin: 0;
  padding: 0;
}
body {
  font-family: Georgia, "Palatino Linotype", "Times New Roman", serif;
  font-size: 1em;
  line-height: 1.55;
  margin: 1em 4%;
}
h1, h2, h3 {
  font-weight: 700;
  line-height: 1.25;
  margin: 1.4em 0 0.7em;
  page-break-after: avoid;
}
h1 {
  font-size: 1.7em;
  margin-top: 0;
}
h2 { font-size: 1.35em; }
h3 { font-size: 1.15em; }
p {
  margin: 0 0 0.85em;
  text-align: justify;
  text-indent: 1.15em;
}
p.first {
  text-indent: 0;
}
.figure {
  margin: 1.1em 0;
  text-align: center;
  text-indent: 0;
}
.figure img {
  max-width: 100%;
  height: auto;
}
""".strip()


def write_epub(document: Document, destination: Path | str, progress: ProgressFn | None = None) -> Path:
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    report(progress, 88, "Writing EPUB…")

    book = epub.EpubBook()
    book.set_identifier(f"pdftoepub-{uuid.uuid4()}")
    book.set_title(document.title)
    book.set_language(document.language or "en")
    if document.author:
        book.add_author(document.author)
    book.add_metadata("DC", "publisher", "PdfToEpub")
    if document.source_name:
        book.add_metadata("DC", "source", document.source_name)

    css = epub.EpubItem(
        uid="style",
        file_name="style/book.css",
        media_type="text/css",
        content=_STYLE.encode("utf-8"),
    )
    book.add_item(css)

    for image in document.images:
        book.add_item(
            epub.EpubItem(
                uid=image.uid,
                file_name=image.file_name,
                media_type=image.media_type,
                content=image.data,
            )
        )

    spine: list = ["nav"]
    toc: list = []
    chapters_html: list[epub.EpubHtml] = []

    for index, chapter in enumerate(document.chapters, start=1):
        file_name = f"chap_{index:03d}.xhtml"
        item = epub.EpubHtml(
            title=chapter.title,
            file_name=file_name,
            lang=document.language or "en",
        )
        item.content = _chapter_html(chapter)
        item.add_link(href="style/book.css", rel="stylesheet", type="text/css")
        book.add_item(item)
        chapters_html.append(item)
        spine.append(item)
        toc.append(epub.Link(file_name, chapter.title, f"chap{index:03d}"))

    book.toc = toc
    book.spine = spine
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())
    epub.write_epub(str(destination), book, {})
    report(progress, 100, "Finished")
    return destination


def _chapter_html(chapter: Chapter) -> str:
    parts: list[str] = []
    first_paragraph = True
    for block in chapter.blocks:
        parts.append(_render_block(block, first_paragraph))
        if block.kind == "heading":
            first_paragraph = True
        elif block.kind == "paragraph" and block.text.strip():
            first_paragraph = False
        elif block.kind == "image":
            first_paragraph = True
    return "\n".join(part for part in parts if part)


def _render_block(block: Block, first_paragraph: bool) -> str:
    if block.kind == "image" and block.image is not None:
        src = html.escape(block.image.file_name)
        return f'<p class="figure first"><img src="{src}" alt=""/></p>'
    text = html.escape(block.text)
    if block.kind == "heading":
        level = min(max(block.level, 1), 3)
        return f"<h{level}>{text}</h{level}>"
    css_class = ' class="first"' if first_paragraph else ""
    return f"<p{css_class}>{text}</p>"


def default_output_path(source_name: str, output_dir: Path | None = None) -> Path:
    stem = Path(source_name).stem or "document"
    safe = re.sub(r"[^\w\s-]", "", stem).strip() or "document"
    name = f"{safe}.epub"
    return (output_dir or Path.cwd()) / name
