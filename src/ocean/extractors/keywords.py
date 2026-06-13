from __future__ import annotations

import re
from dataclasses import dataclass

from ocean.extractors.matcher import KeywordMatcher, KeywordMatchOptions
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
    granularity: str = "paragraph",
    use_regex: bool = False,
    case_sensitive: bool = True,
    normalize_chinese: bool = False,
    deduplicate: bool = True,
) -> list[ExtractionResult]:
    if not keywords:
        return []

    granularity = (granularity or "paragraph").lower()
    if granularity == "page":
        results = _extract_keyword_pages(document, keywords, match_mode, use_regex, case_sensitive, normalize_chinese)
        return _deduplicate_results(results) if deduplicate else results
    if granularity != "paragraph":
        raise ValueError(f"Unsupported keyword extraction granularity: {granularity}")
    results = _extract_keyword_paragraphs(
        document=document,
        keywords=keywords,
        match_mode=match_mode,
        context_before=context_before,
        context_after=context_after,
        use_regex=use_regex,
        case_sensitive=case_sensitive,
        normalize_chinese=normalize_chinese,
    )
    return _deduplicate_results(results) if deduplicate else results


def _extract_keyword_paragraphs(
    document: OcrDocument,
    keywords: list[str],
    match_mode: str,
    context_before: int,
    context_after: int,
    use_regex: bool,
    case_sensitive: bool,
    normalize_chinese: bool,
) -> list[ExtractionResult]:
    paragraphs = _collect_paragraphs(document)
    matcher = KeywordMatcher(
        keywords,
        KeywordMatchOptions(
            match_mode=match_mode,
            use_regex=use_regex,
            case_sensitive=case_sensitive,
            normalize_chinese=normalize_chinese,
        ),
    )
    results: list[ExtractionResult] = []
    for index, paragraph in enumerate(paragraphs):
        match = matcher.match(paragraph.text)
        if not match.is_match:
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
                matched_keywords=match.matched_keywords,
                text=text,
                metadata={"granularity": "paragraph"},
            )
        )
    return results


def _extract_keyword_pages(
    document: OcrDocument,
    keywords: list[str],
    match_mode: str,
    use_regex: bool,
    case_sensitive: bool,
    normalize_chinese: bool,
) -> list[ExtractionResult]:
    matcher = KeywordMatcher(
        keywords,
        KeywordMatchOptions(
            match_mode=match_mode,
            use_regex=use_regex,
            case_sensitive=case_sensitive,
            normalize_chinese=normalize_chinese,
        ),
    )
    results: list[ExtractionResult] = []
    for page in document.pages:
        match = matcher.match(page.text)
        if not match.is_match:
            continue
        results.append(
            ExtractionResult(
                result_id=f"K{len(results) + 1:04d}",
                source_file=document.source_file,
                page_start=page.page_number,
                page_end=page.page_number,
                extraction_method="keyword",
                matched_keywords=match.matched_keywords,
                text=page.text,
                metadata={"granularity": "page"},
            )
        )
    return results


def _deduplicate_results(results: list[ExtractionResult]) -> list[ExtractionResult]:
    deduped: list[ExtractionResult] = []
    for result in results:
        existing = _find_merge_target(deduped, result)
        if existing is None:
            deduped.append(result)
            continue
        existing.page_start = min(existing.page_start, result.page_start)
        existing.page_end = max(existing.page_end, result.page_end)
        existing.text = _merge_text(existing.text, result.text)
        existing.matched_keywords = sorted(set(existing.matched_keywords) | set(result.matched_keywords))
        existing.metadata["merged_result_ids"] = existing.metadata.get("merged_result_ids", []) + [result.result_id]
    for index, result in enumerate(deduped, start=1):
        result.result_id = f"K{index:04d}"
    return deduped


def _find_merge_target(results: list[ExtractionResult], candidate: ExtractionResult) -> ExtractionResult | None:
    for result in results:
        if result.source_file != candidate.source_file:
            continue
        if result.page_end < candidate.page_start or candidate.page_end < result.page_start:
            continue
        if _normalized_text(result.text) == _normalized_text(candidate.text):
            return result
        if _shared_paragraphs(result.text, candidate.text):
            return result
    return None


def _shared_paragraphs(left: str, right: str) -> bool:
    left_parts = {_normalized_text(part) for part in _split_paragraph_text(left)}
    right_parts = {_normalized_text(part) for part in _split_paragraph_text(right)}
    left_parts.discard("")
    right_parts.discard("")
    return bool(left_parts & right_parts)


def _merge_text(left: str, right: str) -> str:
    parts: list[str] = []
    seen: set[str] = set()
    for part in _split_paragraph_text(left) + _split_paragraph_text(right):
        normalized = _normalized_text(part)
        if not normalized or normalized in seen:
            continue
        parts.append(part.strip())
        seen.add(normalized)
    return "\n\n".join(parts)


def _split_paragraph_text(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]


def _normalized_text(text: str) -> str:
    return re.sub(r"\s+", "", text)


def _collect_paragraphs(document: OcrDocument) -> list[Paragraph]:
    paragraphs: list[Paragraph] = []
    for page in document.pages:
        parts = [part.strip() for part in re.split(r"\n\s*\n", page.text) if part.strip()]
        if not parts and page.text.strip():
            parts = [page.text.strip()]
        for part in parts:
            paragraphs.append(Paragraph(document.source_file, page.page_number, part))
    return paragraphs

