from __future__ import annotations

from pathlib import Path

from ocean.models import ExtractionResult, OcrDocument


def write_ocr_markdown(document: OcrDocument, output_path: str | Path) -> None:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"# {document.source_file}", "", f"<!-- ocr_engine: {document.ocr_engine} -->", ""]
    for page in document.pages:
        if page.text.strip():
            lines.extend([page.text.strip(), ""])
    path.write_text("\n".join(lines), encoding="utf-8")


def write_extraction_markdown(results: list[ExtractionResult], output_path: str | Path) -> None:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# 提取结果", ""]
    for result in results:
        lines.extend(
            [
                f"## {result.result_id}",
                "",
                f"- 来源文件：{result.source_file}",
                f"- 页码：第 {result.page_start}-{result.page_end} 页"
                if result.page_start != result.page_end
                else f"- 页码：第 {result.page_start} 页",
                f"- 提取方式：{result.extraction_method}",
            ]
        )
        if result.matched_keywords:
            lines.append(f"- 命中关键词：{', '.join(result.matched_keywords)}")
        if result.topic:
            lines.append(f"- 主题：{result.topic}")
        if result.relevance:
            lines.append(f"- 相关性：{result.relevance}")
        if result.reason:
            lines.extend([f"- 判断理由：{result.reason}"])
        lines.extend(["", result.text.strip(), ""])
    path.write_text("\n".join(lines), encoding="utf-8")
