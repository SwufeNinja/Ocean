from __future__ import annotations

import copy
import json
import os
import uuid
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from ocean.exporters import (
    read_ocr_json,
    write_extraction_csv,
    write_extraction_json,
    write_extraction_markdown,
    write_ocr_json,
    write_ocr_markdown,
)
from ocean.extractors import chunk_by_pages, extract_keywords, extract_semantic
from ocean.logging_utils import log, set_log_file
from ocean.llm import OpenAICompatibleClient
from ocean.models import ExtractionResult, OcrDocument
from ocean.ocr import create_ocr_client
from ocean.pdf_loader import list_pdfs
from ocean.pdf_utils import PdfPart, count_pdf_pages, split_pdf


def run_ocr(input_path: str | Path, output_dir: str | Path, config: dict[str, Any]) -> list[OcrDocument]:
    ocr_config = config.get("ocr", {})
    pdfs = list_pdfs(input_path)
    output = Path(output_dir) / "ocr"
    report_path = output / "run_report.json"
    set_log_file(Path(output_dir) / "ocr_run.log")
    log(f"OCR task started. Input: {input_path}. PDFs found: {len(pdfs)}.")
    report: dict[str, Any] = {
        "run_id": uuid.uuid4().hex,
        "input_path": str(Path(input_path).expanduser().resolve()),
        "output_path": str(output.resolve()),
        "started_at": _now(),
        "finished_at": None,
        "status": "processing",
        "total_files": len(pdfs),
        "success_count": 0,
        "failed_count": 0,
        "files": [],
    }
    _write_run_report(report, report_path)

    primary_engine = str(ocr_config.get("engine") or "")
    primary_client = create_ocr_client(ocr_config)
    fallback_config = _fallback_ocr_config(config)
    fallback_client = create_ocr_client(fallback_config) if fallback_config else None
    documents: list[OcrDocument] = []
    for index, pdf in enumerate(pdfs, start=1):
        file_report: dict[str, Any] = {
            "file_id": uuid.uuid5(uuid.NAMESPACE_URL, str(pdf.resolve())).hex,
            "source_file": pdf.name,
            "source_path": str(pdf.resolve()),
            "status": "processing",
            "page_count": None,
            "started_at": _now(),
            "finished_at": None,
            "ocr_engine": None,
            "fallback_used": False,
            "attempts": [],
            "outputs": {},
            "error": None,
        }
        report["files"].append(file_report)
        _write_run_report(report, report_path)
        log(f"[{index}/{len(pdfs)}] Start PDF: {pdf.name}")
        try:
            file_report["page_count"] = count_pdf_pages(pdf)
            document, used_engine, fallback_used = _recognize_with_fallback(
                pdf=pdf,
                primary_client=primary_client,
                primary_config=ocr_config,
                primary_engine=primary_engine,
                fallback_client=fallback_client,
                fallback_config=fallback_config,
                file_report=file_report,
            )
            stem = pdf.stem
            if ocr_config.get("output_json", True):
                json_path = output / f"{stem}.json"
                write_ocr_json(document, json_path)
                file_report["outputs"]["json"] = str(json_path.resolve())
                log(f"[{index}/{len(pdfs)}] JSON exported: {json_path}")
            if ocr_config.get("output_markdown", True):
                markdown_path = output / f"{stem}.md"
                write_ocr_markdown(document, markdown_path)
                file_report["outputs"]["markdown"] = str(markdown_path.resolve())
                log(f"[{index}/{len(pdfs)}] Markdown exported: {markdown_path}")
            documents.append(document)
            file_report["status"] = "success"
            file_report["ocr_engine"] = used_engine
            file_report["fallback_used"] = fallback_used
            report["success_count"] += 1
            log(f"[{index}/{len(pdfs)}] Finished PDF: {pdf.name}")
        except Exception as exc:
            file_report["status"] = "failed"
            file_report["error"] = str(exc)
            report["failed_count"] += 1
            log(f"[{index}/{len(pdfs)}] Failed PDF: {pdf.name}. Error: {exc}")
        finally:
            file_report["finished_at"] = _now()
            _write_run_report(report, report_path)

    report["finished_at"] = _now()
    report["status"] = "success" if not report["failed_count"] else (
        "failed" if not report["success_count"] else "partial_success"
    )
    _write_run_report(report, report_path)
    log(
        f"OCR task finished. Success: {report['success_count']}; "
        f"failed: {report['failed_count']}; report: {report_path}."
    )
    return documents


def _recognize_with_fallback(
    pdf: Path,
    primary_client: Any,
    primary_config: dict[str, Any],
    primary_engine: str,
    fallback_client: Any | None,
    fallback_config: dict[str, Any] | None,
    file_report: dict[str, Any],
) -> tuple[OcrDocument, str, bool]:
    try:
        document = _recognize_pdf_with_split(primary_client, pdf, primary_config)
        file_report["attempts"].append({"engine": primary_engine, "status": "success", "error": None})
        return document, primary_engine, False
    except Exception as primary_error:
        file_report["attempts"].append(
            {"engine": primary_engine, "status": "failed", "error": str(primary_error)}
        )
        if not fallback_client or not fallback_config:
            raise

        fallback_engine = str(fallback_config.get("engine") or "")
        log(f"{pdf.name}: primary engine {primary_engine} failed; trying fallback engine {fallback_engine}.")
        try:
            document = _recognize_pdf_with_split(fallback_client, pdf, fallback_config)
            file_report["attempts"].append({"engine": fallback_engine, "status": "success", "error": None})
            return document, fallback_engine, True
        except Exception as fallback_error:
            file_report["attempts"].append(
                {"engine": fallback_engine, "status": "failed", "error": str(fallback_error)}
            )
            raise RuntimeError(
                f"Primary OCR engine {primary_engine} failed: {primary_error}; "
                f"fallback engine {fallback_engine} failed: {fallback_error}"
            ) from fallback_error


def _fallback_ocr_config(config: dict[str, Any]) -> dict[str, Any] | None:
    primary = config.get("ocr", {})
    fallback_engine = str(primary.get("fallback_engine") or "").strip()
    if not fallback_engine or fallback_engine.lower() == str(primary.get("engine") or "").lower():
        return None

    fallback = copy.deepcopy(primary)
    engine_configs = config.get("ocr_engines", {})
    fallback.update(copy.deepcopy(engine_configs.get(fallback_engine, {})))
    fallback["engine"] = fallback_engine
    fallback.pop("fallback_engine", None)
    if fallback_engine.lower() in {"paddle", "paddleocr"}:
        fallback["api_base_url"] = (
            engine_configs.get(fallback_engine, {}).get("api_base_url")
            or os.getenv("PADDLEOCR_API_BASE_URL")
            or "https://paddleocr.aistudio-app.com/api/v2/ocr/jobs"
        )
        fallback["api_token"] = (
            engine_configs.get(fallback_engine, {}).get("api_token")
            or os.getenv("PADDLEOCR_API_TOKEN", "")
        )
    elif fallback_engine.lower() in {"mineru", "mineruocr"}:
        fallback["api_base_url"] = (
            engine_configs.get(fallback_engine, {}).get("api_base_url")
            or os.getenv("MINERU_API_BASE_URL")
            or "https://mineru.net"
        )
        fallback["api_token"] = (
            engine_configs.get(fallback_engine, {}).get("api_token")
            or os.getenv("MINERU_API_TOKEN", "")
        )
    return fallback


def _write_run_report(report: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _recognize_pdf_with_split(client: Any, pdf: Path, ocr_config: dict[str, Any]) -> OcrDocument:
    options = ocr_config.get("options", {})
    max_pages = int(options.get("max_pages_per_file", 200))
    total_pages = count_pdf_pages(pdf)
    log(f"{pdf.name}: detected {total_pages} page(s).")
    if total_pages <= max_pages:
        log(f"{pdf.name}: within {max_pages}-page limit; sending directly to OCR.")
        return client.recognize_pdf(pdf, options)

    with TemporaryDirectory(prefix="ocean_pdf_split_") as temp_dir:
        log(f"{pdf.name}: exceeds {max_pages} pages; splitting locally.")
        parts = split_pdf(pdf, temp_dir, max_pages=max_pages)
        log(f"{pdf.name}: split into {len(parts)} part(s).")
        part_documents = []
        for part_index, part in enumerate(parts, start=1):
            log(
                f"{pdf.name}: OCR part {part_index}/{len(parts)} "
                f"(original pages {part.page_start}-{part.page_end})."
            )
            part_document = client.recognize_pdf(part.path, options)
            _offset_document_pages(part_document, part)
            part_documents.append(part_document)
            log(f"{pdf.name}: OCR part {part_index}/{len(parts)} finished.")
    log(f"{pdf.name}: merging {len(part_documents)} OCR part(s) into original page numbers.")
    return _merge_part_documents(pdf, part_documents, total_pages, max_pages)


def run_keyword_extraction(ocr_dir: str | Path, output_dir: str | Path, config: dict[str, Any]) -> list[ExtractionResult]:
    extraction_config = config.get("extraction", {})
    documents = load_ocr_documents(ocr_dir)
    results: list[ExtractionResult] = []
    for document in documents:
        results.extend(
            extract_keywords(
                document=document,
                keywords=extraction_config.get("keywords", []),
                match_mode=extraction_config.get("keyword_match_mode", "any"),
                context_before=int(extraction_config.get("context_before_paragraphs", 0)),
                context_after=int(extraction_config.get("context_after_paragraphs", 0)),
                granularity=extraction_config.get("keyword_granularity", "paragraph"),
                use_regex=bool(extraction_config.get("keyword_use_regex", False)),
                case_sensitive=bool(extraction_config.get("keyword_case_sensitive", True)),
                normalize_chinese=bool(extraction_config.get("keyword_normalize_chinese", False)),
                deduplicate=bool(extraction_config.get("keyword_deduplicate", True)),
            )
        )
    _renumber_results(results, "K")
    return _write_extraction_outputs(results, output_dir, "keywords")


def run_semantic_extraction(ocr_dir: str | Path, output_dir: str | Path, config: dict[str, Any]) -> list[ExtractionResult]:
    extraction_config = config.get("extraction", {})
    documents = load_ocr_documents(ocr_dir)
    llm_client = OpenAICompatibleClient.from_config(config.get("llm", {}))
    chunk_pages = int(extraction_config.get("chunk_pages", 3))
    topics = extraction_config.get("semantic_topics", [])

    results: list[ExtractionResult] = []
    for document in documents:
        chunks = chunk_by_pages(document, pages_per_chunk=chunk_pages)
        results.extend(extract_semantic(chunks, topics, llm_client))
    _renumber_results(results, "S")
    return _write_extraction_outputs(results, output_dir, "semantic")


def load_ocr_documents(ocr_dir: str | Path) -> list[OcrDocument]:
    directory = Path(ocr_dir).expanduser().resolve()
    if not directory.exists():
        raise FileNotFoundError(directory)
    documents = [read_ocr_json(path) for path in sorted(directory.glob("*.json"))]
    if not documents:
        raise FileNotFoundError(f"No OCR JSON files found in {directory}")
    return documents


def _offset_document_pages(document: OcrDocument, part: PdfPart) -> None:
    offset = part.page_start - 1
    for page in document.pages:
        page.page_number += offset
        for block_index, block in enumerate(page.blocks, start=1):
            block.page_number += offset
            block.block_id = f"p{block.page_number}_b{block_index}"


def _merge_part_documents(
    pdf: Path, part_documents: list[OcrDocument], total_pages: int, max_pages: int
) -> OcrDocument:
    pages = []
    for document in part_documents:
        pages.extend(document.pages)
    pages.sort(key=lambda page: page.page_number)
    return OcrDocument(
        source_file=pdf.name,
        source_path=str(pdf),
        ocr_engine=part_documents[0].ocr_engine if part_documents else "",
        pages=pages,
        metadata={
            "split_pdf": True,
            "total_pages": total_pages,
            "max_pages_per_file": max_pages,
            "parts": [
                {
                    "source_file": document.source_file,
                    "pages": [page.page_number for page in document.pages],
                    "mineru_result": document.metadata.get("mineru_result"),
                    "ocr_metadata": document.metadata,
                }
                for document in part_documents
            ],
        },
    )


def _write_extraction_outputs(
    results: list[ExtractionResult], output_dir: str | Path, task_name: str
) -> list[ExtractionResult]:
    output = Path(output_dir) / "extract"
    write_extraction_json(results, output / f"{task_name}.json")
    write_extraction_markdown(results, output / f"{task_name}.md")
    write_extraction_csv(results, output / f"{task_name}.csv")
    return results


def _renumber_results(results: list[ExtractionResult], prefix: str) -> None:
    for index, result in enumerate(results, start=1):
        result.result_id = f"{prefix}{index:04d}"
