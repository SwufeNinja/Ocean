from __future__ import annotations

import re
from dataclasses import dataclass

from ocean.models import ExtractionResult, OcrDocument


@dataclass(slots=True)
class Paragraph:
    source_file: str
    page_number: int
    text: str


def extract_keywords(
    document: OcrDocument,
    keywords: list[str],
    match_mode: str = "any",
    context_before: int = 0,
    context_after: int = 0,
) -> list[ExtractionResult]:
    if not keywords:
        return []

    paragraphs = _collect_paragraphs(document)
    results: list[ExtractionResult] = []
    for index, paragraph in enumerate(paragraphs):
        matched = _matched_keywords(paragraph.text, keywords)
        is_match = bool(matched) if match_mode == "any" else len(matched) == len(keywords)
        if not is_match:
            continue
        start = max(0, index - context_before)
        end = min(len(paragraphs), index + context_after + 1)
        context = paragraphs[start:end]
        text = "\n\n".join(item.text for item in context)
        result_id = f"K{len(results) + 1:04d}"
        results.append(
            ExtractionResult(
                result_id=result_id,
                source_file=document.source_file,
                page_start=min(item.page_number for item in context),
                page_end=max(item.page_number for item in context),
                extraction_method="keyword",
                matched_keywords=matched,
                text=text,
            )
        )
    return results


def _collect_paragraphs(document: OcrDocument) -> list[Paragraph]:
    paragraphs: list[Paragraph] = []
    for page in document.pages:
        parts = [part.strip() for part in re.split(r"\n\s*\n", page.text) if part.strip()]
        if not parts and page.text.strip():
            parts = [page.text.strip()]
        for part in parts:
            paragraphs.append(Paragraph(document.source_file, page.page_number, part))
    return paragraphs


def _matched_keywords(text: str, keywords: list[str]) -> list[str]:
    return [keyword for keyword in keywords if keyword and keyword in text]
