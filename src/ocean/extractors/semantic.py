from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any

from ocean.logging_utils import log
from ocean.llm import OpenAICompatibleClient
from ocean.models import ExtractionResult, TextChunk

_SYSTEM_PROMPT = """你是严谨的中文学术材料整理助手。你的任务是从 OCR 文本中提取与指定主题相关的原文。不要改写原文，不要编造内容。只输出 JSON。"""


def extract_semantic(
    chunks: list[TextChunk],
    topics: list[dict[str, str]],
    llm_client: OpenAICompatibleClient,
    failure_callback: Callable[[dict[str, Any]], None] | None = None,
) -> list[ExtractionResult]:
    results: list[ExtractionResult] = []
    for topic in topics:
        topic_name = topic.get("name", "")
        topic_description = topic.get("description", "")
        for chunk in chunks:
            try:
                response = llm_client.chat(_build_messages(chunk, topic_name, topic_description))
                items = _parse_items(response)
            except Exception as exc:
                _record_failure(
                    failure_callback,
                    chunk=chunk,
                    topic=topic_name,
                    error=exc,
                )
                continue
            for item in items:
                try:
                    result_id = f"S{len(results) + 1:04d}"
                    results.append(
                        ExtractionResult(
                            result_id=result_id,
                            source_file=item.get("source_file") or chunk.source_file,
                            page_start=int(item.get("page_start") or chunk.page_start),
                            page_end=int(item.get("page_end") or chunk.page_end),
                            extraction_method="llm",
                            topic=topic_name,
                            relevance=item.get("relevance", ""),
                            reason=item.get("reason", ""),
                            text=item.get("text", ""),
                            metadata={"chunk_id": chunk.chunk_id},
                        )
                    )
                except Exception as exc:
                    _record_failure(
                        failure_callback,
                        chunk=chunk,
                        topic=topic_name,
                        error=exc,
                        item=item,
                    )
    return [result for result in results if result.text.strip()]


def _record_failure(
    failure_callback: Callable[[dict[str, Any]], None] | None,
    *,
    chunk: TextChunk,
    topic: str,
    error: Exception,
    item: dict[str, Any] | None = None,
) -> None:
    failure = {
        "source_file": chunk.source_file,
        "chunk_id": chunk.chunk_id,
        "page_start": chunk.page_start,
        "page_end": chunk.page_end,
        "topic": topic,
        "error": str(error),
    }
    if item is not None:
        failure["item"] = item
    if failure_callback is not None:
        failure_callback(failure)
    log(
        f"Semantic extraction failed for {chunk.source_file} "
        f"{chunk.chunk_id} pages {chunk.page_start}-{chunk.page_end}: {error}"
    )


def _build_messages(chunk: TextChunk, topic_name: str, topic_description: str) -> list[dict[str, str]]:
    user_prompt = f"""
请从下面 OCR 文本中提取与主题相关的原文。

主题名称：{topic_name}
主题说明：{topic_description}

要求：
1. 只提取 OCR 原文，不要改写。
2. 如果没有相关内容，返回 {{"items": []}}。
3. 每条结果必须包含 source_file、page_start、page_end、relevance、reason、text。
4. 页码必须来自文本中的 [Page N] 标记或当前 chunk 的起止页。
5. 只输出 JSON，不要输出 Markdown。

当前 chunk 信息：
source_file: {chunk.source_file}
page_start: {chunk.page_start}
page_end: {chunk.page_end}

OCR 文本：
{chunk.text}
""".strip()
    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]


def _parse_items(response: str) -> list[dict[str, Any]]:
    try:
        data = json.loads(response)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", response, flags=re.S)
        if not match:
            return []
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            return []
    items = data.get("items", [])
    return items if isinstance(items, list) else []
