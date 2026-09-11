from __future__ import annotations

import hashlib
import threading
from collections import OrderedDict
from pathlib import Path

from pdftoepub.models import Block, Chapter, Document, ImageAsset

_MAX_ENTRIES = 8


def _clone_image(image: ImageAsset | None) -> ImageAsset | None:
    if image is None:
        return None
    return ImageAsset(
        uid=image.uid,
        file_name=image.file_name,
        media_type=image.media_type,
        data=image.data,
        width=image.width,
        height=image.height,
    )


def clone_document(document: Document) -> Document:
    chapters = [
        Chapter(
            title=chapter.title,
            blocks=[
                Block(
                    kind=block.kind,
                    text=block.text,
                    level=block.level,
                    image=_clone_image(block.image),
                    rows=[list(row) for row in block.rows],
                )
                for block in chapter.blocks
            ],
        )
        for chapter in document.chapters
    ]
    return Document(
        title=document.title,
        author=document.author,
        language=document.language,
        page_count=document.page_count,
        chapters=chapters,
        images=[img for img in (_clone_image(image) for image in document.images) if img],
        has_text=document.has_text,
        source_name=document.source_name,
        cover=_clone_image(document.cover),
    )


class ExtractCache:
    """Thread-safe LRU cache for extracted documents."""

    def __init__(self, maxsize: int = _MAX_ENTRIES) -> None:
        self.maxsize = maxsize
        self._lock = threading.Lock()
        self._data: OrderedDict[str, Document] = OrderedDict()
        self.hits = 0
        self.misses = 0

    def get(self, key: str) -> Document | None:
        with self._lock:
            cached = self._data.get(key)
            if cached is None:
                self.misses += 1
                return None
            self._data.move_to_end(key)
            self.hits += 1
            return clone_document(cached)

    def put(self, key: str, document: Document) -> None:
        with self._lock:
            self._data[key] = clone_document(document)
            self._data.move_to_end(key)
            while len(self._data) > self.maxsize:
                self._data.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()
            self.hits = 0
            self.misses = 0

    def stats(self) -> dict[str, int]:
        with self._lock:
            return {
                "hits": self.hits,
                "misses": self.misses,
                "size": len(self._data),
                "maxsize": self.maxsize,
            }


_CACHE = ExtractCache()


def extract_cache_key(
    source: Path | str | bytes,
    *,
    include_images: bool,
    ocr: bool,
    source_name: str = "",
) -> str:
    suffix = f"img={int(include_images)}:ocr={int(ocr)}:name={source_name}"
    if isinstance(source, bytes):
        digest = hashlib.sha256(source).hexdigest()
        return f"b:{digest}:{suffix}"
    path = Path(source).resolve()
    stat = path.stat()
    return f"p:{path}:{stat.st_size}:{stat.st_mtime_ns}:{suffix}"


def cache_get(key: str) -> Document | None:
    return _CACHE.get(key)


def cache_put(key: str, document: Document) -> None:
    _CACHE.put(key, document)


def clear_extract_cache() -> None:
    _CACHE.clear()


def extract_cache_stats() -> dict[str, int]:
    return _CACHE.stats()
