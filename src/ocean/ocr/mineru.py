from __future__ import annotations

import io
import json
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

from ocean.models import OcrBlock, OcrDocument, OcrPage
from ocean.logging_utils import log
from ocean.ocr.base import OcrClient


class MineruClient(OcrClient):
    def __init__(self, api_base_url: str = "https://mineru.net", api_token: str = "") -> None:
        self.api_base_url = api_base_url.rstrip("/") or "https://mineru.net"
        self.api_token = api_token

    def recognize_pdf(self, pdf_path: str | Path, options: dict[str, Any] | None = None) -> OcrDocument:
        if not self.api_token:
            raise ValueError("MinerU api_token is required. Set MINERU_API_TOKEN or ocr.api_token.")

        options = options or {}
        pdf = Path(pdf_path).expanduser().resolve()
        log(f"MinerU: applying upload URL for {pdf.name}.")
        batch_id, upload_url = self._apply_upload_url(pdf, options)
        log(f"MinerU: upload URL received for {pdf.name}; batch_id={batch_id}.")
        log(f"MinerU: uploading {pdf.name} ({pdf.stat().st_size / 1024 / 1024:.2f} MB).")
        self._upload_file(upload_url, pdf)
        log(f"MinerU: upload finished for {pdf.name}; polling parse result.")
        result = self._poll_batch_result(batch_id, pdf.name, options)
        zip_url = result.get("full_zip_url")
        if not zip_url:
            raise RuntimeError(f"MinerU finished without full_zip_url: {result}")
        log(f"MinerU: parse done for {pdf.name}; downloading result zip.")
        zip_bytes = self._download(zip_url, timeout=int(options.get("download_timeout_seconds", 120)))
        document = self._normalize_zip(zip_bytes, pdf, result)
        log(f"MinerU: normalized {pdf.name}; pages={len(document.pages)}.")
        return document

    def _apply_upload_url(self, pdf: Path, options: dict[str, Any]) -> tuple[str, str]:
        file_item: dict[str, Any] = {
            "name": pdf.name,
            "data_id": options.get("data_id") or pdf.stem,
            "is_ocr": bool(options.get("is_ocr", True)),
        }
        if options.get("page_ranges"):
            file_item["page_ranges"] = options["page_ranges"]

        payload: dict[str, Any] = {
            "files": [file_item],
            "model_version": options.get("model_version", "vlm"),
            "language": options.get("language", "ch"),
            "enable_table": bool(options.get("enable_table", True)),
            "enable_formula": bool(options.get("enable_formula", True)),
        }
        if options.get("extra_formats"):
            payload["extra_formats"] = options["extra_formats"]

        data = self._request_json("POST", "/api/v4/file-urls/batch", payload)
        batch_id = data["data"]["batch_id"]
        file_urls = data["data"].get("file_urls") or []
        if not file_urls:
            raise RuntimeError(f"MinerU did not return an upload URL: {data}")
        return batch_id, file_urls[0]

    def _poll_batch_result(self, batch_id: str, file_name: str, options: dict[str, Any]) -> dict[str, Any]:
        poll_interval = int(options.get("poll_interval_seconds", 5))
        max_wait = int(options.get("max_wait_seconds", 1800))
        deadline = time.time() + max_wait
        last_result: dict[str, Any] | None = None

        while time.time() < deadline:
            data = self._request_json("GET", f"/api/v4/extract-results/batch/{batch_id}")
            extract_results = data.get("data", {}).get("extract_result", [])
            if isinstance(extract_results, dict):
                extract_results = [extract_results]
            current = _find_result_for_file(extract_results, file_name) or (extract_results[0] if extract_results else {})
            last_result = current
            state = current.get("state")
            log(f"MinerU: batch_id={batch_id} file={file_name} state={state}.")
            if state == "done":
                return current
            if state == "failed":
                raise RuntimeError(f"MinerU parse failed for {file_name}: {current.get('err_msg')}")
            time.sleep(poll_interval)

        raise TimeoutError(f"MinerU parse timed out for {file_name}. Last result: {last_result}")

    def _request_json(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(
            f"{self.api_base_url}{path}",
            data=body,
            method=method,
            headers={
                "Authorization": f"Bearer {self.api_token}",
                "Content-Type": "application/json",
                "Accept": "*/*",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                data = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"MinerU HTTP {exc.code}: {error_body}") from exc

        if data.get("code") != 0:
            raise RuntimeError(f"MinerU API error: {data}")
        return data

    def _upload_file(self, upload_url: str, pdf: Path) -> None:
        # urllib adds application/x-www-form-urlencoded by default when data is present.
        # OSS pre-signed URLs require the upload headers to match the signed headers.
        request = urllib.request.Request(
            upload_url,
            data=pdf.read_bytes(),
            method="PUT",
            headers={"Content-Type": ""},
        )
        try:
            with urllib.request.urlopen(request, timeout=300) as response:
                if response.status >= 300:
                    raise RuntimeError(f"MinerU upload failed with HTTP {response.status}")
        except urllib.error.HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"MinerU upload HTTP {exc.code}: {error_body}") from exc

    def _download(self, url: str, timeout: int) -> bytes:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return response.read()

    def _normalize_zip(self, zip_bytes: bytes, pdf: Path, result: dict[str, Any]) -> OcrDocument:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
            content_list_name = _find_zip_member(archive, "_content_list.json")
            full_md_name = _find_zip_member(archive, "full.md")
            if content_list_name:
                content_list = json.loads(archive.read(content_list_name).decode("utf-8"))
                return _document_from_content_list(content_list, pdf, result)
            if full_md_name:
                text = archive.read(full_md_name).decode("utf-8")
                return OcrDocument(
                    source_file=pdf.name,
                    source_path=str(pdf),
                    ocr_engine="mineru",
                    pages=[OcrPage(page_number=1, text=text, blocks=[])],
                    metadata={"mineru_result": result, "page_mapping": "unavailable"},
                )
        raise RuntimeError("MinerU result zip does not contain content_list.json or full.md")


def _find_result_for_file(results: list[dict[str, Any]], file_name: str) -> dict[str, Any] | None:
    for result in results:
        if result.get("file_name") == file_name:
            return result
    return None


def _find_zip_member(archive: zipfile.ZipFile, suffix: str) -> str | None:
    for name in archive.namelist():
        if name.endswith(suffix):
            return name
    return None


def _document_from_content_list(content_list: list[dict[str, Any]], pdf: Path, result: dict[str, Any]) -> OcrDocument:
    page_text: dict[int, list[str]] = {}
    page_blocks: dict[int, list[OcrBlock]] = {}

    for index, item in enumerate(content_list, start=1):
        page_number = int(item.get("page_idx", 0)) + 1
        text = _content_item_text(item)
        if not text.strip():
            continue
        page_text.setdefault(page_number, []).append(text)
        page_blocks.setdefault(page_number, []).append(
            OcrBlock(
                block_id=f"p{page_number}_b{len(page_blocks.get(page_number, [])) + 1}",
                text=text,
                page_number=page_number,
                confidence=None,
                metadata={
                    "mineru_index": index,
                    "type": item.get("type"),
                    "bbox": item.get("bbox"),
                    "text_level": item.get("text_level"),
                },
            )
        )

    pages = [
        OcrPage(page_number=page_number, text="\n\n".join(parts), blocks=page_blocks.get(page_number, []))
        for page_number, parts in sorted(page_text.items())
    ]
    return OcrDocument(
        source_file=pdf.name,
        source_path=str(pdf),
        ocr_engine="mineru",
        pages=pages,
        metadata={"mineru_result": result},
    )


def _content_item_text(item: dict[str, Any]) -> str:
    item_type = item.get("type")
    if item_type in {"text", "equation", "list", "code"}:
        return str(item.get("text") or item.get("code_body") or "")
    if item_type == "table":
        parts = item.get("table_caption", []) + [item.get("table_body", "")] + item.get("table_footnote", [])
        return "\n".join(str(part) for part in parts if part)
    if item_type in {"image", "chart"}:
        caption_key = f"{item_type}_caption"
        footnote_key = f"{item_type}_footnote"
        parts = item.get(caption_key, []) + item.get(footnote_key, [])
        return "\n".join(str(part) for part in parts if part)
    return str(item.get("text") or "")
