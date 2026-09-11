from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

BlockKind = Literal["heading", "paragraph", "image", "table"]


@dataclass
class ImageAsset:
    uid: str
    file_name: str
    media_type: str
    data: bytes
    width: int
    height: int


@dataclass
class Block:
    kind: BlockKind
    text: str = ""
    level: int = 0
    image: ImageAsset | None = None
    rows: list[list[str]] = field(default_factory=list)


@dataclass
class Chapter:
    title: str
    blocks: list[Block] = field(default_factory=list)


@dataclass
class Document:
    title: str
    author: str
    language: str
    page_count: int
    chapters: list[Chapter]
    images: list[ImageAsset]
    has_text: bool
    source_name: str = ""
    cover: ImageAsset | None = None


class ConversionError(Exception):
    """User-facing conversion failure."""


class EncryptedPdfError(ConversionError):
    pass


class EmptyDocumentError(ConversionError):
    pass
