from __future__ import annotations

import json
import mimetypes
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

from ocean.logging_utils import log
from ocean.models import OcrBlock, OcrDocument, OcrPage
from ocean.ocr.base import OcrClient


_RETRYABLE_PADDLE_ERROR_CODES = {10010, 12002}
_RETRYABLE_HTTP_STATUS_CODES = {429, 500, 502, 503, 504}


class PaddleOcrHttpError(RuntimeError):
    """HTTP error returned by the PaddleOCR hosted API."""

    def __init__(
        self,
        status_code: int,
        body: str,
        error_code: int | None = None,
        error_message: str | None = None,
    ) -> None:
        self.status_code = status_code
        self.body = body
        self.error_code = error_code
        self.error_message = error_message
        super().__init__(f"PaddleOCR HTTP {status_code}: {body}")

    @property
    def is_retryable(self) -> bool:
        return self.status_code in _RETRYABLE_HTTP_STATUS_CODES or self.error_code in _RETRYABLE_PADDLE_ERROR_CODES


class PaddleOcrClient(OcrClient):
    """PaddleOCR official hosted async Job API client."""

    def __init__(self, api_base_url: str = "", api_key: str = "") -> None:
        self.api_base_url = api_base_url.rstrip("/")
        self.api_key = api_key

    def recognize_pdf(self, pdf_path: str | Path, options: dict[str, Any] | None = None) -> OcrDocument:
        if not self.api_base_url:
            raise ValueError("PaddleOCR official API requires ocr.api_base_url.")
        if not self.api_key:
            raise ValueError("PaddleOCR official API requires PADDLEOCR_API_TOKEN or ocr.api_token.")

        options = options or {}
        pdf = Path(pdf_path).expanduser().resolve()
        log(f"PaddleOCR: submitting {pdf.name} to official hosted job API.")
        job_id = self._submit_job(pdf, options)
        log(f"PaddleOCR: job submitted for {pdf.name}; job_id={job_id}.")

        job_data = self._poll_job(job_id, options)
        result_url = job_data.get("resultUrl", {}).get("jsonUrl")
        if not result_url:
            raise RuntimeError(f"PaddleOCR official job finished without resultUrl.jsonUrl: {job_data}")

        log(f"PaddleOCR: downloading JSONL result for {pdf.name}.")
        jsonl_text = self._download_text(result_url, timeout=int(options.get("download_timeout_seconds", 600)))
        document = _document_from_jsonl(jsonl_text, pdf)
        document.metadata.update(
            {
                "paddleocr_api_mode": "official_hosted",
                "paddleocr_job_id": job_id,
                "paddleocr_job": job_data,
            }
        )
        log(f"PaddleOCR: normalized {pdf.name}; pages={len(document.pages)}.")
        return document

    def _submit_job(self, pdf: Path, options: dict[str, Any]) -> str:
        fields = {
            "model": str(options.get("model", "PaddleOCR-VL-1.6")),
            "optionalPayload": json.dumps(_optional_payload(options), ensure_ascii=False),
        }
        content_type = mimetypes.guess_type(pdf.name)[0] or "application/pdf"
        body, headers = _encode_multipart_form(
            fields=fields,
            files={"file": (pdf.name, pdf.read_bytes(), content_type)},
        )
        headers["Accept"] = "application/json"
        self._add_auth_header(headers, options)

        data = self._request_json_with_retries(
            self.api_base_url,
            method="POST",
            body=body,
            headers=headers,
            timeout=int(options.get("timeout_seconds", 600)),
            options=options,
            operation=f"submit {pdf.name}",
        )
        try:
            return str(data["data"]["jobId"])
        except KeyError as exc:
            raise RuntimeError(f"PaddleOCR official job API did not return data.jobId: {data}") from exc

    def _poll_job(self, job_id: str, options: dict[str, Any]) -> dict[str, Any]:
        poll_interval = int(options.get("poll_interval_seconds", 5))
        max_wait = int(options.get("max_wait_seconds", 1800))
        deadline = time.time() + max_wait
        last_data: dict[str, Any] | None = None

        while time.time() < deadline:
            headers = {"Accept": "application/json"}
            self._add_auth_header(headers, options)
            data = self._request_json_with_retries(
                f"{self.api_base_url}/{job_id}",
                method="GET",
                body=None,
                headers=headers,
                timeout=int(options.get("timeout_seconds", 600)),
                options=options,
                operation=f"poll job {job_id}",
            )
            job_data = data.get("data", {})
            last_data = job_data
            state = job_data.get("state")
            progress = job_data.get("extractProgress", {})
            total_pages = progress.get("totalPages")
            extracted_pages = progress.get("extractedPages")
            if total_pages is not None and extracted_pages is not None:
                log(f"PaddleOCR: job_id={job_id} state={state}; pages={extracted_pages}/{total_pages}.")
            else:
                log(f"PaddleOCR: job_id={job_id} state={state}.")

            if state == "done":
                return job_data
            if state == "failed":
                raise RuntimeError(f"PaddleOCR official job failed: {job_data.get('errorMsg')}")
            time.sleep(poll_interval)

        raise TimeoutError(f"PaddleOCR official job timed out. Last result: {last_data}")

    def _request_json(
        self, url: str, method: str, body: bytes | None, headers: dict[str, str], timeout: int
    ) -> dict[str, Any]:
        request = urllib.request.Request(url, data=body, method=method, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="replace")
            error_code, error_message = _parse_paddle_error_body(error_body)
            raise PaddleOcrHttpError(exc.code, error_body, error_code, error_message) from exc

    def _request_json_with_retries(
        self,
        url: str,
        method: str,
        body: bytes | None,
        headers: dict[str, str],
        timeout: int,
        options: dict[str, Any],
        operation: str,
    ) -> dict[str, Any]:
        initial_delay = int(options.get("retry_initial_delay_seconds", 30))
        max_delay = int(options.get("retry_max_delay_seconds", 300))
        max_wait = int(options.get("retry_max_wait_seconds", 1800))
        deadline = time.time() + max_wait
        delay = max(initial_delay, 1)
        attempt = 1

        while True:
            try:
                return self._request_json(url, method=method, body=body, headers=headers, timeout=timeout)
            except PaddleOcrHttpError as exc:
                remaining_wait = deadline - time.time()
                if not exc.is_retryable or remaining_wait <= 0:
                    raise

                sleep_seconds = min(delay, max_delay, int(remaining_wait))
                if sleep_seconds <= 0:
                    raise

                code_text = f" code={exc.error_code}" if exc.error_code is not None else ""
                message_text = f" msg={exc.error_message}" if exc.error_message else ""
                log(
                    f"PaddleOCR: retryable HTTP {exc.status_code}{code_text}{message_text} "
                    f"during {operation}; retrying in {sleep_seconds}s (attempt {attempt})."
                )
                time.sleep(sleep_seconds)
                delay = min(delay * 2, max_delay)
                attempt += 1

    def _download_text(self, url: str, timeout: int) -> str:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return response.read().decode("utf-8")

    def _add_auth_header(self, headers: dict[str, str], options: dict[str, Any]) -> None:
        auth_scheme = str(options.get("auth_scheme", "bearer")).strip()
        if auth_scheme:
            headers["Authorization"] = f"{auth_scheme} {self.api_key}"
        else:
            headers["Authorization"] = self.api_key


def _optional_payload(options: dict[str, Any]) -> dict[str, Any]:
    option_map = {
        "use_doc_orientation_classify": "useDocOrientationClassify",
        "use_doc_unwarping": "useDocUnwarping",
        "use_chart_recognition": "useChartRecognition",
        "use_table_recognition": "useTableRecognition",
        "use_formula_recognition": "useFormulaRecognition",
        "use_seal_recognition": "useSealRecognition",
        "use_region_detection": "useRegionDetection",
    }
    payload = {target_key: options[source_key] for source_key, target_key in option_map.items() if source_key in options}
    if "api_extra_payload" in options and isinstance(options["api_extra_payload"], dict):
        payload.update(options["api_extra_payload"])
    return payload


def _parse_paddle_error_body(body: str) -> tuple[int | None, str | None]:
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return None, None
    error_code = payload.get("code")
    if isinstance(error_code, str) and error_code.isdigit():
        error_code = int(error_code)
    if not isinstance(error_code, int):
        error_code = None
    error_message = payload.get("msg")
    if not isinstance(error_message, str):
        error_message = None
    return error_code, error_message


def _encode_multipart_form(
    fields: dict[str, str], files: dict[str, tuple[str, bytes, str]]
) -> tuple[bytes, dict[str, str]]:
    boundary = f"----OCRAssistantPaddleOCR{uuid.uuid4().hex}"
    chunks: list[bytes] = []

    for name, value in fields.items():
        chunks.extend(
            [
                f"--{boundary}\r\n".encode("utf-8"),
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8"),
                value.encode("utf-8"),
                b"\r\n",
            ]
        )

    for name, (filename, content, content_type) in files.items():
        chunks.extend(
            [
                f"--{boundary}\r\n".encode("utf-8"),
                (
                    f'Content-Disposition: form-data; name="{name}"; '
                    f'filename="{filename}"\r\nContent-Type: {content_type}\r\n\r\n'
                ).encode("utf-8"),
                content,
                b"\r\n",
            ]
        )

    chunks.append(f"--{boundary}--\r\n".encode("utf-8"))
    return b"".join(chunks), {"Content-Type": f"multipart/form-data; boundary={boundary}"}


def _document_from_jsonl(jsonl_text: str, pdf: Path) -> OcrDocument:
    pages: list[OcrPage] = []
    jsonl_line_count = 0

    for line_number, raw_line in enumerate(jsonl_text.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        jsonl_line_count += 1
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"PaddleOCR JSONL line {line_number} is not valid JSON: {raw_line[:200]}") from exc

        result = item.get("result") or {}
        for layout_result in result.get("layoutParsingResults") or []:
            page_number = len(pages) + 1
            text = _markdown_text(layout_result)
            block = OcrBlock(
                block_id=f"p{page_number}_b1",
                text=text,
                page_number=page_number,
                confidence=None,
                metadata={"type": "markdown", "paddleocr_page_index": page_number - 1},
            )
            pages.append(OcrPage(page_number=page_number, text=text, blocks=[block] if text.strip() else []))

    return OcrDocument(
        source_file=pdf.name,
        source_path=str(pdf),
        ocr_engine="paddleocr",
        pages=pages,
        metadata={"paddleocr_jsonl_line_count": jsonl_line_count},
    )


def _markdown_text(item: dict[str, Any]) -> str:
    markdown = item.get("markdown") or {}
    if isinstance(markdown, dict):
        return str(markdown.get("text") or "")
    return str(markdown or "")
