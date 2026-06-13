from __future__ import annotations

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
    client = create_ocr_client(ocr_config)
    pdfs = list_pdfs(input_path)
    output = Path(output_dir) / "ocr"
    set_log_file(Path(output_dir) / "ocr_run.log")
    log(f"OCR task started. Input: {input_path}. PDFs found: {len(pdfs)}.")
    documents: list[OcrDocument] = []
    for index, pdf in enumerate(pdfs, start=1):
        log(f"[{index}/{len(pdfs)}] Start PDF: {pdf.name}")
        document = _recognize_pdf_with_split(client, pdf, ocr_config)
        documents.append(document)
        stem = pdf.stem
        if ocr_config.get("output_json", True):
            write_ocr_json(document, output / f"{stem}.json")
            log(f"[{index}/{len(pdfs)}] JSON exported: {output / f'{stem}.json'}")
        if ocr_config.get("output_markdown", True):
            write_ocr_markdown(document, output / f"{stem}.md")
            log(f"[{index}/{len(pdfs)}] Markdown exported: {output / f'{stem}.md'}")
        log(f"[{index}/{len(pdfs)}] Finished PDF: {pdf.name}")
    log(f"OCR task finished. Documents: {len(documents)}.")
    return documents


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
