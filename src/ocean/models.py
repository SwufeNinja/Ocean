from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any


@dataclass(slots=True)
class OcrBlock:
    block_id: str
    text: str
    page_number: int
    confidence: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class OcrPage:
    page_number: int
    text: str
    blocks: list[OcrBlock] = field(default_factory=list)


@dataclass(slots=True)
class OcrDocument:
    source_file: str
    source_path: str
    ocr_engine: str
    pages: list[OcrPage]
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class TextChunk:
    chunk_id: str
    source_file: str
    page_start: int
    page_end: int
    text: str


@dataclass(slots=True)
class ExtractionResult:
    result_id: str
    source_file: str
    page_start: int
    page_end: int
    extraction_method: str
    text: str
    matched_keywords: list[str] = field(default_factory=list)
    topic: str = ""
    relevance: str = ""
    reason: str = ""
    created_at: str = field(default_factory=lambda: datetime.now().astimezone().isoformat(timespec="seconds"))
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def ocr_document_from_dict(data: dict[str, Any]) -> OcrDocument:
    pages: list[OcrPage] = []
    for page_data in data.get("pages", []):
        blocks = [OcrBlock(**block) for block in page_data.get("blocks", [])]
        pages.append(
            OcrPage(
                page_number=int(page_data["page_number"]),
                text=page_data.get("text", ""),
                blocks=blocks,
            )
        )
    return OcrDocument(
        source_file=data.get("source_file", ""),
        source_path=data.get("source_path", ""),
        ocr_engine=data.get("ocr_engine", ""),
        pages=pages,
        metadata=data.get("metadata", {}),
    )
