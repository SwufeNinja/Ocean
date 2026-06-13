from __future__ import annotations

from pathlib import Path
from typing import Any

from ocean.models import OcrDocument
from ocean.ocr.base import OcrClient


class PaddleOcrClient(OcrClient):
    def __init__(self, api_base_url: str = "", api_key: str = "") -> None:
        self.api_base_url = api_base_url
        self.api_key = api_key

    def recognize_pdf(self, pdf_path: str | Path, options: dict[str, Any] | None = None) -> OcrDocument:
        raise NotImplementedError(
            "PaddleOCR API schema is not wired yet. Test the real API response first, "
            "then map it to OcrDocument in this client."
        )
